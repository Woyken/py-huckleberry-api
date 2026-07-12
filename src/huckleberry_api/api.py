"""API client for Huckleberry."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Callable, Literal, TypeVar, cast
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
from google.api_core.exceptions import GoogleAPICallError
from google.auth.credentials import Credentials
from google.cloud import firestore
from google.cloud.firestore import DELETE_FIELD
from google.cloud.firestore_v1 import AsyncClient
from pydantic import TypeAdapter, ValidationError

from .const import AUTH_URL, FIREBASE_API_KEY, REFRESH_URL
from .firebase_types import (
    ActivityMode,
    BottleType,
    DiaperMode,
    FeedSide,
    FirebaseActivityDocumentData,
    FirebaseActivityIntervalData,
    FirebaseActivityMultiContainer,
    FirebaseBottleFeedIntervalData,
    FirebaseBreastFeedIntervalData,
    FirebaseChildDocument,
    FirebaseCuratedFoodDocument,
    FirebaseCustomFoodTypeDocument,
    FirebaseDiaperData,
    FirebaseDiaperDocumentData,
    FirebaseDiaperMultiContainer,
    FirebaseDiaperQuantity,
    FirebaseFeedDocumentData,
    FirebaseFeedIntervalData,
    FirebaseFeedMultiContainer,
    FirebaseFeedTimerData,
    FirebaseGrowthData,
    FirebaseHealthDocumentData,
    FirebaseHealthMultiContainer,
    FirebaseLastActivityData,
    FirebaseLastBottleData,
    FirebaseLastDiaperData,
    FirebaseLastNursingData,
    FirebaseLastPottyData,
    FirebaseLastPumpData,
    FirebaseLastSideData,
    FirebaseLastSleepData,
    FirebaseLastSolidData,
    FirebasePumpDocumentData,
    FirebasePumpIntervalData,
    FirebasePumpMultiContainer,
    FirebaseSleepCondition,
    FirebaseSleepDetails,
    FirebaseSleepDocumentData,
    FirebaseSleepIntervalData,
    FirebaseSleepLocations,
    FirebaseSleepMultiContainer,
    FirebaseSleepTimerData,
    FirebaseSolidsFeedIntervalData,
    FirebaseTimestamp,
    FirebaseTypesAvailableTypes,
    FirebaseTypesDocument,
    FirebaseUserDocument,
    HealthDataEntry,
    PooColor,
    PooConsistency,
    PottyResult,
    PumpEntryMode,
    SolidsFoodEntry,
    SolidsReaction,
    VolumeUnits,
    to_firebase_dict,
)
from .models import SolidsFoodReference

CURATED_FOODS_BUCKET = "simpleintervals.appspot.com"
CURATED_FOODS_OBJECT = "foods/fooddb.json"

# Type variable for listener callback typing
TDocumentData = TypeVar(
    "TDocumentData",
    FirebaseSleepDocumentData,
    FirebaseFeedDocumentData,
    FirebaseHealthDocumentData,
    FirebaseDiaperDocumentData,
    FirebaseActivityDocumentData,
    FirebasePumpDocumentData,
    FirebaseChildDocument,
)

_LOGGER = logging.getLogger(__name__)

# Epoch seconds are ~1.8e9; values beyond this can only be milliseconds.
_MS_EPOCH_THRESHOLD = 1e11


def _ensure_epoch_seconds(value: float) -> float:
    """Normalize an epoch value that may be in milliseconds to seconds.

    The official iOS app writes feed `timer.feedStartTime` in milliseconds
    (observed live, 2026-07), while library-started sessions store seconds.
    History intervals must always use seconds; a millisecond value written
    verbatim dates the entry ~56,000 years in the future and freezes the
    official app's summary pages.
    """
    return value / 1000 if value > _MS_EPOCH_THRESHOLD else value


_FEED_INTERVAL_ADAPTER = TypeAdapter(FirebaseFeedIntervalData)
_HEALTH_ENTRY_ADAPTER = TypeAdapter(HealthDataEntry)
_ACTIVITY_LAST_FIELD_BY_MODE: dict[ActivityMode, str] = {
    "bath": "lastBath",
    "brushTeeth": "lastBrushTeeth",
    "indoorPlay": "lastIndoorPlay",
    "outdoorPlay": "lastOutdoorPlay",
    "screenTime": "lastScreenTime",
    "skinToSkin": "lastSkinToSkin",
    "storyTime": "lastStoryTime",
    "tummyTime": "lastTummyTime",
}


async def _raise_for_status_with_details(response: aiohttp.ClientResponse, operation: str) -> None:
    """Raise an aiohttp status error with the raw response payload included."""
    if response.status < 400:
        return

    message = f"{operation} failed with HTTP {response.status}"
    if response.reason:
        message = f"{message} {response.reason}"

    try:
        response_payload = await response.json(content_type=None)
        message = f"{message}: {json.dumps(response_payload, separators=(',', ':'))}"
    except aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError, ValueError:
        response_body = await response.text()
        if response_body:
            message = f"{message}: {response_body}"

    raise aiohttp.ClientResponseError(
        response.request_info,
        response.history,
        status=response.status,
        message=message,
        headers=response.headers,
    )


class FirebaseTokenCredentials(Credentials):
    """Custom credentials class for Firebase SDK."""

    def __init__(self, id_token: str):
        """Initialize with Firebase ID token."""
        super().__init__()
        self._id_token = id_token
        self.token = id_token  # Set the token attribute that parent expects

    def refresh(self, request):
        """Token refresh is handled by HuckleberryAPI.

        This method is required by the Credentials interface but is not used.
        Token refreshing is managed externally by HuckleberryAPI.refresh_session_token(),
        and a new FirebaseTokenCredentials instance is created with the refreshed token.
        """


class HuckleberryAPI:
    """API client for Huckleberry."""

    def __init__(self, email: str, password: str, timezone: str, websession: aiohttp.ClientSession) -> None:
        """Initialize the API client.

        Args:
            email: User email for authentication.
            password: User password for authentication.
            timezone: IANA timezone string (e.g., "America/New_York", "Europe/London").
            websession: Shared aiohttp client session for outbound HTTP requests.
        """
        self.email = email
        self.password = password
        self.websession = websession
        self.id_token: str | None = None
        self.refresh_token: str | None = None
        self.user_uid: str | None = None
        self.token_expires_at: float | None = None
        self._firestore_client: AsyncClient | None = None
        self._firestore_client_loop: asyncio.AbstractEventLoop | None = None
        self._listener_client: firestore.Client | None = None
        self._timezone = ZoneInfo(timezone)
        self._listeners: dict = {}  # Store active listeners
        self._listener_callbacks: dict = {}  # Store callbacks to recreate listeners

    async def authenticate(self) -> None:
        """Authenticate with Firebase."""
        _LOGGER.debug("Authenticating with Huckleberry")

        try:
            async with self.websession.post(
                f"{AUTH_URL}?key={FIREBASE_API_KEY}",
                json={
                    "email": self.email,
                    "password": self.password,
                    "returnSecureToken": True,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                await _raise_for_status_with_details(response, "Authentication")
                data = await response.json()
            self.id_token = data["idToken"]
            self.refresh_token = data["refreshToken"]
            self.user_uid = data["localId"]
            self.token_expires_at = datetime.now().timestamp() + int(data["expiresIn"])

            _LOGGER.info("Successfully authenticated with Huckleberry")
        except aiohttp.ClientResponseError as err:
            _LOGGER.error("Authentication failed: status=%s message=%s", err.status, err.message)
            raise
        except aiohttp.ClientError as err:
            _LOGGER.error("Authentication request failed: %s", err)
            raise

    async def ensure_session(self) -> None:
        """Ensure there is a valid authenticated session.

        This should be called periodically (for example by a coordinator) to keep
        long-lived listeners healthy across token expiration windows.
        """
        await self._ensure_authenticated()

    async def refresh_session_token(self) -> None:
        """Refresh the Firebase authentication token."""
        if not self.refresh_token:
            raise ValueError("No refresh token available")

        _LOGGER.debug("Refreshing authentication token")

        try:
            async with self.websession.post(
                f"{REFRESH_URL}?key={FIREBASE_API_KEY}",
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                await _raise_for_status_with_details(response, "Token refresh")
                data = await response.json()
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to refresh authentication token: %s", err)
            raise
        except ValueError as err:
            _LOGGER.error("Invalid refresh token response payload: %s", err)
            raise

        self.id_token = data["id_token"]
        self.refresh_token = data["refresh_token"]
        self.token_expires_at = datetime.now().timestamp() + int(data["expires_in"])

        # Stop existing listeners (they use the old token)
        for key, watch in self._listeners.items():
            try:
                if hasattr(watch, "unsubscribe") and callable(getattr(watch, "unsubscribe")):
                    watch.unsubscribe()
                elif hasattr(watch, "close") and callable(getattr(watch, "close")):
                    watch.close()
                _LOGGER.debug("Stopped listener %s before token refresh", key)
            except (AttributeError, RuntimeError, TypeError, ValueError) as err:
                _LOGGER.error("Error stopping listener %s before refresh: %s", key, err)
        self._listeners.clear()

        # Invalidate both Firestore clients so they get recreated with new token.
        # The async client is used for reads/writes; the sync client backs the
        # snapshot listeners. Without resetting the sync client, recreated
        # listeners reuse the old credentials and silently stop receiving
        # updates after the first token refresh (~1 hour).
        self._firestore_client = None
        self._firestore_client_loop = None
        self._listener_client = None

        _LOGGER.debug("Successfully refreshed authentication token")

        # Recreate all listeners with new token
        _LOGGER.info("Recreating %d listeners with refreshed token", len(self._listener_callbacks))
        callbacks_copy = dict(self._listener_callbacks)  # Copy to avoid modification during iteration
        for key, (listener_type, child_uid, callback) in callbacks_copy.items():
            try:
                if listener_type == "sleep":
                    await self.setup_sleep_listener(child_uid, callback)
                elif listener_type == "feed":
                    await self.setup_feed_listener(child_uid, callback)
                elif listener_type == "health":
                    await self.setup_health_listener(child_uid, callback)
                elif listener_type == "diaper":
                    await self.setup_diaper_listener(child_uid, callback)
                elif listener_type == "activities":
                    await self.setup_activity_listener(child_uid, callback)
                elif listener_type == "pump":
                    await self.setup_pump_listener(child_uid, callback)
                elif listener_type == "childs":
                    await self.setup_child_listener(child_uid, callback)
                _LOGGER.debug("Recreated %s listener for child %s", listener_type, child_uid)
            except (GoogleAPICallError, RuntimeError, TypeError, ValueError) as err:
                _LOGGER.error("Error recreating %s listener for child %s: %s", listener_type, child_uid, err)

    async def _ensure_authenticated(self) -> None:
        """Ensure we have a valid authentication token."""
        if not self.id_token:
            await self.authenticate()
        elif self.token_expires_at and datetime.now().timestamp() >= self.token_expires_at - 300:
            # Refresh if token expires in less than 5 minutes
            await self.refresh_session_token()

    async def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        await self._ensure_authenticated()
        return {
            "Authorization": f"Bearer {self.id_token}",
            "Content-Type": "application/json",
        }

    async def _get_firestore_client(self) -> AsyncClient:
        """Get or create Firestore client."""
        await self._ensure_authenticated()
        current_loop = asyncio.get_running_loop()

        # Recreate when missing or when crossing asyncio event loop boundaries.
        # grpc.aio channels are loop-bound and cannot be reused across loops.
        if self._firestore_client and self._firestore_client_loop is not current_loop:
            self._firestore_client = None
            self._firestore_client_loop = None

        if not self._firestore_client:
            assert self.id_token is not None, "id_token should be set after authentication"
            credentials = FirebaseTokenCredentials(self.id_token)
            self._firestore_client = AsyncClient(
                project="simpleintervals",
                credentials=credentials,
            )
            self._firestore_client_loop = current_loop

        return self._firestore_client

    async def _get_timezone_offset_minutes(self) -> float:
        """Get current timezone offset in minutes.

        Calculates offset dynamically to handle DST changes.
        Returns negative for UTC+ timezones (e.g., -120 for UTC+2).
        """
        now = datetime.now(self._timezone)
        offset = now.utcoffset()
        if offset is None:
            return 0.0
        return -offset.total_seconds() / 60

    async def get_child(self, child_uid: str) -> FirebaseChildDocument | None:
        """Get a single child document by child UID."""
        _LOGGER.debug("Fetching child document for %s", child_uid)

        try:
            db = await self._get_firestore_client()
            child_doc_ref = db.collection("childs").document(child_uid)
            child_doc = await child_doc_ref.get()

            if not child_doc.exists:
                _LOGGER.warning("Child document not found: %s", child_uid)
                return None

            child_data = child_doc.to_dict()
            if not child_data:
                _LOGGER.warning("Child document has no data: %s", child_uid)
                return None

            return FirebaseChildDocument.model_validate(child_data)

        except (GoogleAPICallError, ValidationError, RuntimeError, TypeError, ValueError) as err:
            _LOGGER.error("Failed to get child %s: %s", child_uid, err)
            raise

    async def get_user(self) -> FirebaseUserDocument | None:
        """Get full users/{uid} document as strict Firebase model."""
        _LOGGER.debug("Fetching user document")

        db = await self._get_firestore_client()
        user_ref = db.collection("users").document(self.user_uid)
        user_doc = await user_ref.get()

        if not user_doc.exists:
            _LOGGER.error("User document not found")
            return None

        user_data = user_doc.to_dict()
        if not user_data:
            _LOGGER.error("User document has no data")
            return None

        return FirebaseUserDocument.model_validate(user_data)

    async def start_sleep(self, child_uid: str) -> None:
        """Start sleep tracking for a child."""
        _LOGGER.info("Starting sleep tracking for child %s", child_uid)

        client = await self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)

        current_time = time.time()
        current_time_ms = current_time * 1000  # Milliseconds for timerStartTime

        # Generate a unique session UUID (16 hex characters like the app)
        session_uuid = uuid.uuid4().hex[:16]

        # Update the timer field to mark sleep as active
        # Match the structure from the Huckleberry app
        sleep_data = FirebaseSleepDocumentData(
            timer=FirebaseSleepTimerData(
                active=True,
                paused=False,
                timestamp=FirebaseTimestamp(seconds=current_time),
                local_timestamp=current_time,
                timerStartTime=current_time_ms,
                uuid=session_uuid,
                details=FirebaseSleepDetails(
                    startSleepCondition=FirebaseSleepCondition.model_validate(
                        {
                            "happy": False,
                            "longTimeToFallAsleep": False,
                            "10-20_minutes": False,
                            "upset": False,
                            "under_10_minutes": False,
                        }
                    ),
                    sleepLocations=FirebaseSleepLocations(
                        car=False,
                        nursing=False,
                        wornOrHeld=False,
                        stroller=False,
                        coSleep=False,
                        nextToCarer=False,
                        onOwnInBed=False,
                        bottle=False,
                        swing=False,
                    ),
                    endSleepCondition=FirebaseSleepCondition(
                        happy=False,
                        wokeUpChild=False,
                        upset=False,
                    ),
                ),
            )
        )
        await sleep_ref.set(to_firebase_dict(sleep_data), merge=True)

        _LOGGER.info("Sleep tracking started successfully")

    async def pause_sleep(self, child_uid: str) -> None:
        """Pause current sleep session without ending it."""
        _LOGGER.info("Pausing sleep for child %s", child_uid)

        client = await self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)

        # Check if timer is active
        sleep_doc = await sleep_ref.get(timeout=10.0)
        if not sleep_doc.exists:
            _LOGGER.warning("No sleep document to pause for %s", child_uid)
            return

        sleep_data = FirebaseSleepDocumentData.model_validate(sleep_doc.to_dict() or {})
        timer = sleep_data.timer
        if not timer or not timer.active:
            _LOGGER.info("Sleep is not active for %s, ignoring pause request", child_uid)
            return

        if timer.paused:
            _LOGGER.info("Sleep is already paused for %s", child_uid)
            return

        now = time.time()
        timer_end_time_ms = now * 1000  # Convert to milliseconds

        # Add timerEndTime field that app uses to show end time when paused
        await sleep_ref.update(
            {
                "timer.paused": True,
                "timer.active": True,
                "timer.timerEndTime": timer_end_time_ms,
                "timer.timestamp": {"seconds": now},
                "timer.local_timestamp": now,
            }
        )

        _LOGGER.info("Sleep paused for child %s", child_uid)

    async def resume_sleep(self, child_uid: str) -> None:
        """Resume a paused sleep session."""
        _LOGGER.info("Resuming sleep for child %s", child_uid)

        client = await self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)

        # Check if timer is active and paused
        sleep_doc = await sleep_ref.get(timeout=10.0)
        if not sleep_doc.exists:
            _LOGGER.warning("No sleep document to resume for %s", child_uid)
            return

        sleep_data = FirebaseSleepDocumentData.model_validate(sleep_doc.to_dict() or {})
        timer = sleep_data.timer
        if not timer or not timer.active:
            _LOGGER.info("Sleep is not active for %s, ignoring resume request", child_uid)
            return

        if not timer.paused:
            _LOGGER.info("Sleep is not paused for %s, ignoring resume request", child_uid)
            return

        now = time.time()
        await sleep_ref.update(
            {
                "timer.paused": False,
                "timer.active": True,
                "timer.timestamp": {"seconds": now},
                "timer.local_timestamp": now,
            }
        )

        _LOGGER.info("Sleep resumed for child %s", child_uid)

    async def cancel_sleep(self, child_uid: str) -> None:
        """Cancel current sleep session without saving an interval."""
        _LOGGER.info("Cancelling current sleep for child %s", child_uid)

        client = await self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)

        # Check current state
        doc = await sleep_ref.get(timeout=10.0)
        if doc.exists:
            sleep_data = FirebaseSleepDocumentData.model_validate(doc.to_dict() or {})
            timer = sleep_data.timer
            if timer:
                _LOGGER.info("Current timer state: active=%s, paused=%s", timer.active, timer.paused)
                session_uuid = timer.uuid
            else:
                session_uuid = uuid.uuid4().hex[:16]
        else:
            _LOGGER.warning("Sleep document does not exist for child %s", child_uid)
            session_uuid = uuid.uuid4().hex[:16]

        # Set timer to inactive (don't delete it - app expects it to remain)
        current_time = time.time()
        await sleep_ref.update(
            {
                "timer": {
                    "active": False,
                    "paused": False,
                    "timestamp": {"seconds": current_time},
                    "timerStartTime": None,
                    "uuid": session_uuid,
                    "local_timestamp": current_time,
                },
            }
        )

        _LOGGER.info("Sleep cancelled for child %s", child_uid)

    async def complete_sleep(self, child_uid: str) -> None:
        """Complete current sleep session and save interval."""
        _LOGGER.info("Completing sleep for child %s", child_uid)

        client = await self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)

        sleep_doc = await sleep_ref.get(timeout=10.0)
        if not sleep_doc.exists:
            _LOGGER.warning("No active sleep document to complete for %s", child_uid)
            return

        sleep_data = FirebaseSleepDocumentData.model_validate(sleep_doc.to_dict() or {})
        timer = sleep_data.timer

        # Check if timer is already inactive (already completed)
        if not timer or not timer.active:
            _LOGGER.info("Sleep already completed for %s, ignoring duplicate request", child_uid)
            return

        timer_start_ms = timer.timerStartTime
        if not timer_start_ms:
            # Attempt fallback: reconstruct using timestamp.seconds if available
            ts_seconds = timer.timestamp.seconds if timer.timestamp else None
            if ts_seconds:
                timer_start_ms = int(float(ts_seconds) * 1000)
                _LOGGER.warning("timerStartTime missing; falling back to timestamp.seconds for %s", child_uid)
            else:
                _LOGGER.warning("Missing timerStartTime; cannot compute duration for %s", child_uid)
                await sleep_ref.update({"timer": firestore.DELETE_FIELD})
                return

        now_ms = time.time() * 1000

        # If sleep is paused, use timerEndTime as the end time (not current time)
        if timer.paused and timer.timerEndTime is not None:
            end_ms = timer.timerEndTime
            _LOGGER.info("Sleep is paused, using timerEndTime for completion")
        else:
            end_ms = now_ms

        duration_sec = int((end_ms - float(timer_start_ms)) / 1000)
        start_sec = int(float(timer_start_ms) / 1000)

        intervals_ref = sleep_ref.collection("intervals")
        interval_id = uuid.uuid4().hex[:16]
        sleep_interval = FirebaseSleepIntervalData(
            start=start_sec,
            duration=duration_sec,
            offset=await self._get_timezone_offset_minutes(),
            end_offset=await self._get_timezone_offset_minutes(),
            details=timer.details,
            lastUpdated=time.time(),
        )
        await intervals_ref.document(interval_id).set(to_firebase_dict(sleep_interval))

        # Set timer to inactive (match stop_sleep behavior)
        current_time = time.time()
        session_uuid = timer.uuid

        last_sleep_data = FirebaseLastSleepData(
            start=start_sec,
            duration=duration_sec,
            offset=await self._get_timezone_offset_minutes(),
        )

        await sleep_ref.update(
            {
                "timer": {
                    "active": False,
                    "paused": False,
                    "timestamp": {"seconds": current_time},
                    "timerStartTime": None,
                    "uuid": session_uuid,
                    "local_timestamp": current_time,
                },
                "prefs.lastSleep": to_firebase_dict(last_sleep_data),
                "prefs.timestamp": {"seconds": current_time},
                "prefs.local_timestamp": current_time,
            }
        )

        _LOGGER.info("Sleep completed for child %s (duration %ss)", child_uid, duration_sec)

    async def log_sleep(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        end_time: datetime,
        details: FirebaseSleepDetails | None = None,
    ) -> None:
        """Log a completed sleep interval without using the live timer."""
        start_timestamp = start_time.timestamp()
        end_timestamp = end_time.timestamp()
        start_sec = int(start_timestamp)
        end_sec = int(end_timestamp)
        if end_sec < start_sec:
            raise ValueError("end_time must be after start_time")

        current_time = time.time()
        duration_sec = end_sec - start_sec
        timezone_offset = await self._get_timezone_offset_minutes()
        sleep_interval = FirebaseSleepIntervalData(
            start=start_sec,
            duration=duration_sec,
            offset=timezone_offset,
            end_offset=timezone_offset,
            details=details,
            lastUpdated=current_time,
        )
        last_sleep_data = FirebaseLastSleepData(
            start=start_sec,
            duration=duration_sec,
            offset=timezone_offset,
        )

        client = await self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)
        sleep_doc = await sleep_ref.get()
        sleep_model = FirebaseSleepDocumentData.model_validate(sleep_doc.to_dict() or {})
        existing_last_sleep = sleep_model.prefs.lastSleep if sleep_model.prefs else None
        existing_last_sleep_start = existing_last_sleep.start if existing_last_sleep else None
        should_update_last_sleep = existing_last_sleep_start is None or float(start_sec) >= float(
            existing_last_sleep_start
        )

        await sleep_ref.collection("intervals").document(uuid.uuid4().hex[:16]).set(to_firebase_dict(sleep_interval))

        if should_update_last_sleep:
            await sleep_ref.set(
                {
                    "prefs": {
                        "lastSleep": to_firebase_dict(last_sleep_data),
                        "timestamp": {"seconds": current_time},
                        "local_timestamp": current_time,
                    }
                },
                merge=True,
            )

        _LOGGER.info("Sleep logged for child %s (updated_last=%s)", child_uid, should_update_last_sleep)

    async def start_nursing(self, child_uid: str, side: FeedSide = "left") -> None:
        """Start nursing tracking."""
        _LOGGER.info("Starting nursing for child %s on %s side", child_uid, side)

        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        current_time = time.time()

        session_uuid = uuid.uuid4().hex[:16]

        feed_data = FirebaseFeedDocumentData(
            timer=FirebaseFeedTimerData(
                active=True,
                paused=False,
                timestamp=FirebaseTimestamp(seconds=current_time),
                local_timestamp=current_time,
                feedStartTime=current_time,
                timerStartTime=current_time,
                uuid=session_uuid,
                leftDuration=0.0,
                rightDuration=0.0,
                lastSide="left",
                activeSide=side,
            )
        )
        await feed_ref.set(to_firebase_dict(feed_data), merge=True)

        _LOGGER.info("Nursing started on %s side", side)

    async def pause_nursing(self, child_uid: str) -> None:
        """Pause current nursing session."""
        _LOGGER.info("Pausing nursing for child %s", child_uid)

        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        doc = await feed_ref.get(timeout=10.0)
        if not doc.exists:
            _LOGGER.warning("Feed document not found")
            return

        feed_data = FirebaseFeedDocumentData.model_validate(doc.to_dict() or {})
        timer = feed_data.timer
        if not timer:
            _LOGGER.warning("Feed document has no timer")
            return

        if not timer.active:
            _LOGGER.info("Nursing is not active for %s, ignoring pause request", child_uid)
            return

        if timer.paused:
            _LOGGER.info("Nursing is already paused for %s", child_uid)
            return
        current_side = timer.activeSide or timer.lastSide or "left"

        # Calculate elapsed time and accumulate to current side
        now = time.time()
        timer_start = timer.timerStartTime or now
        elapsed = now - timer_start

        left_duration = timer.leftDuration or 0.0
        right_duration = timer.rightDuration or 0.0

        if current_side == "left":
            left_duration += elapsed
        else:
            right_duration += elapsed

        await feed_ref.update(
            {
                "timer.paused": True,
                "timer.active": True,
                "timer.timestamp": {"seconds": now},
                "timer.local_timestamp": now,
                "timer.leftDuration": left_duration,
                "timer.rightDuration": right_duration,
                "timer.lastSide": current_side,
                "timer.activeSide": DELETE_FIELD,
            }
        )

        _LOGGER.info("Nursing paused (L:%ss R:%ss)", left_duration, right_duration)

    async def resume_nursing(self, child_uid: str, side: FeedSide | None = None) -> None:
        """Resume paused nursing session."""
        _LOGGER.info("Resuming nursing for child %s", child_uid)

        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        doc = await feed_ref.get(timeout=10.0)
        if not doc.exists:
            _LOGGER.warning("Feed document not found")
            return

        feed_data = FirebaseFeedDocumentData.model_validate(doc.to_dict() or {})
        timer = feed_data.timer
        if not timer:
            _LOGGER.warning("Feed document has no timer")
            return

        if not timer.active:
            _LOGGER.info("Nursing is not active for %s, ignoring resume request", child_uid)
            return

        if not timer.paused:
            _LOGGER.info("Nursing is not paused for %s, ignoring resume request", child_uid)
            return
        if side is None:
            side = timer.lastSide or "left"

        now = time.time()

        await feed_ref.update(
            {
                "timer.paused": False,
                "timer.active": True,
                "timer.timestamp": {"seconds": now},
                "timer.local_timestamp": now,
                "timer.timerStartTime": now,  # Reset timer start time on resume
                "timer.activeSide": side,
                "timer.lastSide": "none",  # Set to none during transition
            }
        )

        _LOGGER.info("Nursing resumed on %s", side)

    async def switch_nursing_side(self, child_uid: str) -> None:
        """Switch nursing side (left <-> right)."""
        _LOGGER.info("Switching nursing side for child %s", child_uid)

        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        doc = await feed_ref.get(timeout=10.0)
        if not doc.exists:
            _LOGGER.warning("Feed document not found")
            return

        feed_data = FirebaseFeedDocumentData.model_validate(doc.to_dict() or {})
        timer = feed_data.timer
        if not timer:
            _LOGGER.warning("Feed document has no timer")
            return

        if not timer.active:
            _LOGGER.info("Nursing is not active for %s, ignoring switch request", child_uid)
            return
        current_side = timer.activeSide or timer.lastSide or "left"
        new_side = "right" if current_side == "left" else "left"
        is_paused = timer.paused

        now = time.time()
        left_duration = timer.leftDuration or 0.0
        right_duration = timer.rightDuration or 0.0

        # Only accumulate duration if NOT paused
        if not is_paused:
            # Calculate duration since timer started and accumulate to current side
            timer_start = timer.timerStartTime or now
            elapsed = now - timer_start

            if current_side == "left":
                left_duration += elapsed
            else:
                right_duration += elapsed

        update_data = {
            "timer.paused": False,  # Switching always resumes
            "timer.lastSide": "none",  # Set to none during transition
            "timer.timestamp": {"seconds": now},
            "timer.local_timestamp": now,
            "timer.timerStartTime": now,  # Always reset timer start time
            "timer.activeSide": new_side,  # Always set active side
            "timer.leftDuration": left_duration,
            "timer.rightDuration": right_duration,
        }

        await feed_ref.update(update_data)

        _LOGGER.info("Switched from %s to %s (L:%ss R:%ss)", current_side, new_side, left_duration, right_duration)

    async def cancel_nursing(self, child_uid: str) -> None:
        """Cancel current nursing without saving."""
        _LOGGER.info("Cancelling nursing for child %s", child_uid)

        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        doc = await feed_ref.get(timeout=10.0)
        if doc.exists:
            feed_data = FirebaseFeedDocumentData.model_validate(doc.to_dict() or {})
            timer = feed_data.timer
            if timer:
                session_uuid = timer.uuid
            else:
                session_uuid = uuid.uuid4().hex[:16]
        else:
            session_uuid = uuid.uuid4().hex[:16]

        current_time = time.time()
        await feed_ref.update(
            {
                "timer": {
                    "active": False,
                    "paused": False,
                    "timestamp": {"seconds": current_time},
                    "timerStartTime": None,
                    "uuid": session_uuid,
                    "local_timestamp": current_time,
                    "leftDuration": 0.0,
                    "rightDuration": 0.0,
                    "lastSide": "left",
                },
            }
        )

        _LOGGER.info("Nursing cancelled")

    async def complete_nursing(self, child_uid: str) -> None:
        """Complete current nursing and save to history."""
        _LOGGER.info("Completing nursing for child %s", child_uid)

        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        doc = await feed_ref.get(timeout=10.0)
        if not doc.exists:
            _LOGGER.warning("No active feed document to complete")
            return

        feed_data = FirebaseFeedDocumentData.model_validate(doc.to_dict() or {})
        timer = feed_data.timer

        # Check if timer is already inactive (already completed)
        if not timer or not timer.active:
            _LOGGER.info("Nursing already completed for %s, ignoring duplicate request", child_uid)
            return

        timer_start = timer.timerStartTime
        if not timer_start:
            _LOGGER.warning("Missing timerStartTime for nursing")
            return

        now_time = time.time()
        # timerStartTime is in seconds for feeding (normalized defensively)
        timer_start_sec = _ensure_epoch_seconds(float(timer_start))

        left_duration = timer.leftDuration or 0.0
        right_duration = timer.rightDuration or 0.0

        # Add elapsed time on current side if not paused
        if not timer.paused:
            elapsed = now_time - timer_start_sec
            current_side = timer.activeSide or timer.lastSide or "left"

            if current_side == "left":
                left_duration += elapsed
            else:
                right_duration += elapsed

        # Calculate total duration from accumulated durations
        total_duration = left_duration + right_duration

        # App-started sessions store feedStartTime in milliseconds.
        feed_start_time = (
            _ensure_epoch_seconds(float(timer.feedStartTime)) if timer.feedStartTime else timer_start_sec
        )

        # Determine last side for history
        last_side_value = timer.activeSide or timer.lastSide or "right"
        if last_side_value == "none":
            last_side_value = "right" if right_duration >= left_duration else "left"

        # Create interval document ID (format: timestamp-random)
        interval_id = f"{int(now_time * 1000)}-{uuid.uuid4().hex[:20]}"

        # Create interval document for history (feed/{child_uid}/intervals)
        feed_intervals_ref = feed_ref.collection("intervals").document(interval_id)

        breast_interval = FirebaseBreastFeedIntervalData(
            mode="breast",
            start=feed_start_time,
            lastSide=last_side_value,
            lastUpdated=now_time,
            leftDuration=left_duration,
            rightDuration=right_duration,
            offset=await self._get_timezone_offset_minutes(),
            end_offset=await self._get_timezone_offset_minutes(),
        )

        try:
            await feed_intervals_ref.set(to_firebase_dict(breast_interval))
            _LOGGER.info("Created nursing interval entry: %s", interval_id)
        except GoogleAPICallError as err:
            _LOGGER.error("Failed to create nursing interval entry: %s", err)

        last_nursing_data = FirebaseLastNursingData(
            mode="breast",
            start=feed_start_time,
            duration=total_duration,
            leftDuration=left_duration,
            rightDuration=right_duration,
            offset=await self._get_timezone_offset_minutes(),
        )

        last_side_data = FirebaseLastSideData(
            start=feed_start_time,
            lastSide=last_side_value,
        )

        # Update to inactive and save to lastNursing
        await feed_ref.update(
            {
                "timer.active": False,
                "timer.paused": True,
                "timer.timestamp": {"seconds": now_time},
                "timer.local_timestamp": now_time,
                "timer.lastSide": last_side_value,
                "timer.leftDuration": DELETE_FIELD,  # Remove durations from timer
                "timer.rightDuration": DELETE_FIELD,
                "timer.activeSide": DELETE_FIELD,  # Remove activeSide
                "prefs.lastNursing": to_firebase_dict(last_nursing_data),
                "prefs.lastSide": to_firebase_dict(last_side_data),
                "prefs.timestamp": {"seconds": now_time},
                "prefs.local_timestamp": now_time,
            }
        )

        _LOGGER.info(
            "Nursing completed (total duration %ss, L:%ss R:%ss)", total_duration, left_duration, right_duration
        )

    async def log_nursing(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        end_time: datetime,
        side: FeedSide = "left",
        left_duration: float | int | None = None,
        right_duration: float | int | None = None,
    ) -> None:
        """Log a completed nursing interval without using the live timer."""
        start_timestamp = start_time.timestamp()
        end_timestamp = end_time.timestamp()
        if end_timestamp < start_timestamp:
            raise ValueError("end_time must be after start_time")

        if (left_duration is None) != (right_duration is None):
            raise ValueError("Provide both left_duration and right_duration together")

        if left_duration is None and right_duration is None:
            total_duration = float(end_timestamp - start_timestamp)
            resolved_left_duration = total_duration if side == "left" else 0.0
            resolved_right_duration = total_duration if side == "right" else 0.0
        else:
            resolved_left_duration = float(left_duration or 0.0)
            resolved_right_duration = float(right_duration or 0.0)
            if resolved_left_duration < 0 or resolved_right_duration < 0:
                raise ValueError("left_duration and right_duration must be non-negative")
            total_duration = resolved_left_duration + resolved_right_duration

        current_time = time.time()
        interval_id = f"{int(current_time * 1000)}-{uuid.uuid4().hex[:20]}"
        offset = await self._get_timezone_offset_minutes()
        end_offset = await self._get_timezone_offset_minutes()
        breast_interval = FirebaseBreastFeedIntervalData(
            mode="breast",
            start=start_timestamp,
            lastSide=side,
            lastUpdated=current_time,
            leftDuration=resolved_left_duration,
            rightDuration=resolved_right_duration,
            offset=offset,
            end_offset=end_offset,
        )
        last_nursing_data = FirebaseLastNursingData(
            mode="breast",
            start=start_timestamp,
            duration=total_duration,
            leftDuration=resolved_left_duration,
            rightDuration=resolved_right_duration,
            offset=offset,
        )
        last_side_data = FirebaseLastSideData(start=start_timestamp, lastSide=side)

        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)
        feed_doc = await feed_ref.get()
        feed_model = FirebaseFeedDocumentData.model_validate(feed_doc.to_dict() or {})
        existing_last_nursing = feed_model.prefs.lastNursing if feed_model.prefs else None
        existing_last_nursing_start = existing_last_nursing.start if existing_last_nursing else None
        should_update_last_nursing = existing_last_nursing_start is None or start_timestamp >= float(
            existing_last_nursing_start
        )

        await feed_ref.collection("intervals").document(interval_id).set(to_firebase_dict(breast_interval))

        if should_update_last_nursing:
            await feed_ref.set(
                {
                    "prefs": {
                        "lastNursing": to_firebase_dict(last_nursing_data),
                        "lastSide": to_firebase_dict(last_side_data),
                        "timestamp": {"seconds": current_time},
                        "local_timestamp": current_time,
                    }
                },
                merge=True,
            )

        _LOGGER.info("Nursing logged for child %s (updated_last=%s)", child_uid, should_update_last_nursing)

    async def log_bottle(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        amount: float,
        bottle_type: BottleType = "Formula",
        units: VolumeUnits = "ml",
    ) -> None:
        """Log bottle feeding as instant event.

        Args:
            child_uid: Child unique identifier
            start_time: Event time
            bottle_type: Type of bottle contents ("Breast Milk", "Formula", "Cow Milk", etc.)
            amount: Amount fed in specified units
            units: Volume units ("ml" or "oz")
        """
        _LOGGER.info("Logging bottle feeding for child %s: %s %s of %s", child_uid, amount, units, bottle_type)

        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        start_timestamp = start_time.timestamp()
        current_time = time.time()
        interval_id = f"{int(current_time * 1000)}-{uuid.uuid4().hex[:20]}"
        offset = await self._get_timezone_offset_minutes()
        feed_doc = await feed_ref.get()
        feed_model = FirebaseFeedDocumentData.model_validate(feed_doc.to_dict() or {})
        existing_last_bottle = feed_model.prefs.lastBottle if feed_model.prefs else None
        existing_last_bottle_start = existing_last_bottle.start if existing_last_bottle else None
        should_update_last_bottle = existing_last_bottle_start is None or start_timestamp >= float(
            existing_last_bottle_start
        )

        # Create interval document for bottle feeding
        bottle_entry = FirebaseBottleFeedIntervalData(
            mode="bottle",
            start=start_timestamp,
            lastUpdated=current_time,
            bottleType=bottle_type,
            amount=amount,
            units=units,
            offset=offset,
            end_offset=offset,
        )

        # Create interval document
        feed_intervals_ref = feed_ref.collection("intervals").document(interval_id)

        try:
            await feed_intervals_ref.set(to_firebase_dict(bottle_entry))
            _LOGGER.info("Created bottle feeding interval entry: %s", interval_id)
        except GoogleAPICallError as err:
            _LOGGER.error("Failed to create bottle feeding interval entry: %s", err)
            raise RuntimeError(f"Failed to log bottle feeding: {err}") from err

        # Update prefs.lastBottle and document-level bottle preferences
        last_bottle_data = FirebaseLastBottleData(
            mode="bottle",
            start=start_timestamp,
            bottleType=bottle_type,
            bottleAmount=amount,
            bottleUnits=units,
            offset=offset,
        )

        if should_update_last_bottle:
            await feed_ref.set(
                {
                    "prefs": {
                        "lastBottle": to_firebase_dict(last_bottle_data),
                        "bottleType": bottle_type,
                        "bottleAmount": amount,
                        "bottleUnits": units,
                        "timestamp": {"seconds": current_time},
                        "local_timestamp": current_time,
                    }
                },
                merge=True,
            )

        _LOGGER.info(
            "Bottle feeding logged: %s %s of %s (updated_last=%s)",
            amount,
            units,
            bottle_type,
            should_update_last_bottle,
        )

    async def list_solids_curated_foods(self) -> list[FirebaseCuratedFoodDocument]:
        """List curated solids foods from Firebase Storage."""
        await self._ensure_authenticated()

        if not self.id_token:
            raise RuntimeError("Missing authentication token")

        encoded_object = quote(CURATED_FOODS_OBJECT, safe="")
        url = f"https://firebasestorage.googleapis.com/v0/b/{CURATED_FOODS_BUCKET}/o/{encoded_object}?alt=media"

        async with self.websession.get(
            url,
            headers={"Authorization": f"Bearer {self.id_token}"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected curated foods payload shape")

        foods: list[FirebaseCuratedFoodDocument] = []
        for food_data in payload.values():
            if not isinstance(food_data, dict):
                continue

            entry = dict(food_data)
            foods.append(FirebaseCuratedFoodDocument.model_validate(entry))

        return sorted(
            foods,
            key=lambda item: (
                float(item.rank) if item.rank is not None else float("inf"),
                item.name.lower(),
            ),
        )

    async def list_solids_custom_foods(
        self, child_uid: str, include_archived: bool = False
    ) -> list[FirebaseCustomFoodTypeDocument]:
        """List custom solids foods from Firestore ``types/{child_uid}/custom``."""
        client = await self._get_firestore_client()
        custom_ref = client.collection("types").document(child_uid).collection("custom")

        foods: list[FirebaseCustomFoodTypeDocument] = []
        async for doc in custom_ref.where("type", "==", "solids").stream():
            raw_data = doc.to_dict() or {}
            item = FirebaseCustomFoodTypeDocument.model_validate(raw_data)
            if not include_archived and item.archived:
                continue
            foods.append(item)

        return sorted(foods, key=lambda item: item.updated_at, reverse=True)

    async def create_solids_custom_food(
        self, child_uid: str, name: str, image: str = ""
    ) -> FirebaseCustomFoodTypeDocument:
        """Create a custom solids food in types/{child_uid}/custom."""
        food_name = name.strip()
        if not food_name:
            raise ValueError("Custom food name must be non-empty")

        now_iso = datetime.now(dt_timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        food_id = str(uuid.uuid4())

        custom_food = FirebaseCustomFoodTypeDocument(
            created_at=now_iso,
            updated_at=now_iso,
            name=food_name,
            archived=False,
            id=food_id,
            type="solids",
            image=image,
            source="custom",
        )

        client = await self._get_firestore_client()
        types_ref = client.collection("types").document(child_uid)
        types_document = FirebaseTypesDocument(available_types=FirebaseTypesAvailableTypes(solids=True))
        await types_ref.set(to_firebase_dict(types_document), merge=True)
        await types_ref.collection("custom").document(food_id).set(to_firebase_dict(custom_food))

        return custom_food

    async def log_solids(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        foods: list[SolidsFoodReference],
        notes: str = "",
        reaction: SolidsReaction | None = None,
        food_note_image: str | None = None,
    ) -> None:
        """Log solid food feeding.

        Args:
            child_uid: Child unique identifier
            start_time: Event time
            foods: Existing food references with explicit id/source/name/amount
            notes: Optional notes about the meal
            reaction: Optional reaction - "LOVED", "MEH", "HATED", or "ALLERGIC"
            food_note_image: Optional Firebase Storage image filename
        """
        if not foods:
            raise ValueError("At least one food is required")

        _LOGGER.info("Logging solids for child %s with %d foods", child_uid, len(foods))

        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        start_timestamp = start_time.timestamp()
        current_time = time.time()
        interval_id = f"{int(current_time * 1000)}-{uuid.uuid4().hex[:20]}"
        offset = await self._get_timezone_offset_minutes()
        feed_doc = await feed_ref.get()
        feed_model = FirebaseFeedDocumentData.model_validate(feed_doc.to_dict() or {})
        existing_last_solid = feed_model.prefs.lastSolid if feed_model.prefs else None
        existing_last_solid_start = existing_last_solid.start if existing_last_solid else None
        should_update_last_solid = existing_last_solid_start is None or start_timestamp >= float(
            existing_last_solid_start
        )

        foods_dict: dict[str, SolidsFoodEntry] = {}
        for food_item in foods:
            food_ref = (
                food_item
                if isinstance(food_item, SolidsFoodReference)
                else SolidsFoodReference.model_validate(food_item)
            )

            food_name = food_ref.name.strip()
            if not food_name:
                raise ValueError("Food name must be non-empty")

            foods_dict[food_ref.id] = SolidsFoodEntry(
                id=food_ref.id,
                source=food_ref.source,
                created_name=food_name,
                amount=food_ref.amount,
            )

        entry = FirebaseSolidsFeedIntervalData(
            mode="solids",
            start=start_timestamp,
            lastUpdated=current_time,
            offset=offset,
            foods=foods_dict,
        )

        if notes:
            entry.notes = notes
        if reaction:
            entry.reactions = {reaction: True}
        if food_note_image:
            entry.foodNoteImage = food_note_image

        await feed_ref.collection("intervals").document(interval_id).set(to_firebase_dict(entry))

        last_solid = FirebaseLastSolidData(
            mode="solids",
            start=start_timestamp,
            foods=foods_dict,
            reactions={reaction: True} if reaction else None,
            notes=notes if notes else None,
            offset=offset,
        )

        if should_update_last_solid:
            await feed_ref.set(
                {
                    "prefs": {
                        "lastSolid": to_firebase_dict(last_solid),
                        "timestamp": {"seconds": current_time},
                        "local_timestamp": current_time,
                    }
                },
                merge=True,
            )

        _LOGGER.info("Solids logged with %d references (updated_last=%s)", len(foods_dict), should_update_last_solid)

    async def _setup_listener(
        self,
        collection_name: Literal["sleep", "feed", "health", "diaper", "activities", "pump", "childs"],
        child_uid: str,
        callback: Callable[[TDocumentData], None],
    ) -> None:
        """Set up real-time listener for a Firestore document.

        Generic listener setup method that works for any collection type.

        Args:
            collection_name: Name of the Firestore collection (e.g., 'sleep', 'feed', 'health', 'diaper')
            child_uid: Child unique identifier
            callback: Function to call when document changes, receives document data of the appropriate type
        """
        _LOGGER.info("Setting up real-time listener for %s/%s", collection_name, child_uid)

        await self._ensure_authenticated()
        if not self._listener_client:
            assert self.id_token is not None, "id_token should be set after authentication"
            credentials = FirebaseTokenCredentials(self.id_token)
            self._listener_client = firestore.Client(
                project="simpleintervals",
                credentials=credentials,
            )

        doc_ref = self._listener_client.collection(collection_name).document(child_uid)

        # Create snapshot listener
        def on_snapshot(doc_snapshot, _changes, _read_time):
            """Handle snapshot updates."""
            for doc in doc_snapshot:
                if doc.exists:
                    _LOGGER.debug("Real-time %s update received for child %s", collection_name, child_uid)
                    payload = doc.to_dict() or {}
                    if collection_name == "sleep":
                        validated = FirebaseSleepDocumentData.model_validate(payload)
                    elif collection_name == "feed":
                        validated = FirebaseFeedDocumentData.model_validate(payload)
                    elif collection_name == "health":
                        validated = FirebaseHealthDocumentData.model_validate(payload)
                    elif collection_name == "activities":
                        validated = FirebaseActivityDocumentData.model_validate(payload)
                    elif collection_name == "pump":
                        validated = FirebasePumpDocumentData.model_validate(payload)
                    elif collection_name == "childs":
                        validated = FirebaseChildDocument.model_validate(payload)
                    else:
                        validated = FirebaseDiaperDocumentData.model_validate(payload)
                    callback(cast(TDocumentData, validated))

        # Start listening and store the unsubscribe function
        unsubscribe = doc_ref.on_snapshot(on_snapshot)
        listener_key = f"{collection_name}_{child_uid}"
        self._listeners[listener_key] = unsubscribe
        # Store callback for recreation after token refresh
        self._listener_callbacks[listener_key] = (collection_name, child_uid, callback)

        _LOGGER.info("Real-time %s listener active for child %s", collection_name, child_uid)

    async def setup_sleep_listener(self, child_uid: str, callback: Callable[[FirebaseSleepDocumentData], None]) -> None:
        """Set up real-time listener for sleep document changes."""
        await self._setup_listener("sleep", child_uid, callback)

    async def setup_feed_listener(self, child_uid: str, callback: Callable[[FirebaseFeedDocumentData], None]) -> None:
        """Set up real-time listener for feed document changes."""
        await self._setup_listener("feed", child_uid, callback)

    async def setup_health_listener(
        self, child_uid: str, callback: Callable[[FirebaseHealthDocumentData], None]
    ) -> None:
        """Set up real-time listener for health document changes."""
        await self._setup_listener("health", child_uid, callback)

    async def setup_diaper_listener(
        self, child_uid: str, callback: Callable[[FirebaseDiaperDocumentData], None]
    ) -> None:
        """Set up real-time listener for diaper document changes."""
        await self._setup_listener("diaper", child_uid, callback)

    async def setup_activity_listener(
        self, child_uid: str, callback: Callable[[FirebaseActivityDocumentData], None]
    ) -> None:
        """Set up real-time listener for activities document changes."""
        await self._setup_listener("activities", child_uid, callback)

    async def setup_pump_listener(self, child_uid: str, callback: Callable[[FirebasePumpDocumentData], None]) -> None:
        """Set up real-time listener for pump document changes."""
        await self._setup_listener("pump", child_uid, callback)

    async def setup_child_listener(self, child_uid: str, callback: Callable[[FirebaseChildDocument], None]) -> None:
        """Set up real-time listener for child document changes."""
        await self._setup_listener("childs", child_uid, callback)

    async def stop_all_listeners(self) -> None:
        """Stop all active real-time listeners."""
        _LOGGER.info("Stopping all real-time listeners")
        for key, watch in self._listeners.items():
            try:
                if hasattr(watch, "unsubscribe") and callable(getattr(watch, "unsubscribe")):
                    watch.unsubscribe()
                elif hasattr(watch, "close") and callable(getattr(watch, "close")):
                    watch.close()
                else:
                    _LOGGER.debug("Listener %s object has no unsubscribe/close", key)
                _LOGGER.debug("Stopped listener: %s", key)
            except (AttributeError, RuntimeError, TypeError, ValueError) as err:
                _LOGGER.error("Error stopping listener %s: %s", key, err)
        self._listeners.clear()
        self._listener_callbacks.clear()
        self._listener_client = None

    async def _log_diaper_or_potty_event(
        self,
        child_uid: str,
        mode: DiaperMode,
        *,
        start_time: datetime,
        pref_field: Literal["lastDiaper", "lastPotty"],
        pee_amount: Literal["little", "medium", "big"] | None = None,
        poo_amount: Literal["little", "medium", "big"] | None = None,
        color: PooColor | None = None,
        consistency: PooConsistency | None = None,
        diaper_rash: bool = False,
        notes: str | None = None,
        is_potty: bool = False,
        how_it_happened: PottyResult | None = None,
    ) -> None:
        """Write a diaper-collection diaper or potty event and update the matching prefs summary."""
        event_kind = "potty" if is_potty else "diaper"
        _LOGGER.info("Logging %s event for child %s: mode=%s", event_kind, child_uid, mode)

        client = await self._get_firestore_client()
        diaper_ref = client.collection("diaper").document(child_uid)

        start_timestamp = start_time.timestamp()
        current_time = time.time()
        current_offset = await self._get_timezone_offset_minutes()
        diaper_doc = await diaper_ref.get()
        diaper_model = FirebaseDiaperDocumentData.model_validate(diaper_doc.to_dict() or {})
        existing_last_event = getattr(diaper_model.prefs, pref_field, None) if diaper_model.prefs else None
        existing_last_event_start = existing_last_event.start if existing_last_event else None
        should_update_last_event = existing_last_event_start is None or start_timestamp >= float(
            existing_last_event_start
        )

        # Create interval ID (timestamp in ms + random suffix)
        interval_timestamp_ms = int(current_time * 1000)
        interval_id = f"{interval_timestamp_ms}-{uuid.uuid4().hex[:20]}"

        # Build interval data (matching app behavior - minimal fields by default)
        interval_data = FirebaseDiaperData(
            start=start_timestamp,
            lastUpdated=current_time,
            mode=mode,
            offset=current_offset,
        )

        # Add quantity field if amounts are specified
        # App uses: 0.0 = "little", 50.0 = "medium", 100.0 = "big"
        # Other values are treated as no quantity indicator
        amount_map = {"little": 0.0, "medium": 50.0, "big": 100.0}
        quantity: dict[str, float] = {}
        if pee_amount and pee_amount in amount_map:
            quantity["pee"] = amount_map[pee_amount]
        if poo_amount and poo_amount in amount_map:
            quantity["poo"] = amount_map[poo_amount]
        if quantity:
            interval_data.quantity = FirebaseDiaperQuantity(**quantity)

        # Add optional fields if provided
        if color:
            interval_data.color = color
        if consistency:
            interval_data.consistency = consistency
        if diaper_rash:
            interval_data.diaperRash = True
        if notes:
            interval_data.notes = notes
        if is_potty:
            interval_data.isPotty = True
        if how_it_happened:
            interval_data.howItHappened = how_it_happened

        # Create interval document in subcollection
        try:
            await diaper_ref.collection("intervals").document(interval_id).set(to_firebase_dict(interval_data))
            _LOGGER.info("Created %s interval: %s", event_kind, interval_id)
        except GoogleAPICallError as err:
            _LOGGER.error("Failed to create %s interval: %s", event_kind, err)
            raise

        prefs_entry: FirebaseLastDiaperData | FirebaseLastPottyData
        if pref_field == "lastDiaper":
            prefs_entry = FirebaseLastDiaperData(
                start=start_timestamp,
                mode=mode,
                offset=current_offset,
            )
        else:
            prefs_entry = FirebaseLastPottyData(
                start=start_timestamp,
                mode=mode,
                offset=current_offset,
            )

        if should_update_last_event:
            try:
                await diaper_ref.set(
                    {
                        "prefs": {
                            pref_field: to_firebase_dict(prefs_entry),
                            "timestamp": {"seconds": current_time},
                            "local_timestamp": current_time,
                        }
                    },
                    merge=True,
                )
                _LOGGER.info("Updated %s prefs", pref_field)
            except GoogleAPICallError as err:
                _LOGGER.error("Failed to update %s prefs: %s", pref_field, err)
                raise

        _LOGGER.info(
            "%s event logged successfully (updated_last=%s)", event_kind.capitalize(), should_update_last_event
        )

    async def log_diaper(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        mode: DiaperMode,
        pee_amount: Literal["little", "medium", "big"] | None = None,
        poo_amount: Literal["little", "medium", "big"] | None = None,
        color: PooColor | None = None,
        consistency: PooConsistency | None = None,
        diaper_rash: bool = False,
        notes: str | None = None,
    ) -> None:
        """
        Log a diaper change.

        Args:
            child_uid: Child unique identifier
            start_time: Event time
            mode: One of 'pee', 'poo', 'both', 'dry'
            pee_amount: Pee amount - 'little', 'medium', 'big', or None (no quantity)
            poo_amount: Poo amount - 'little', 'medium', 'big', or None (no quantity)
            color: Poo color - 'yellow', 'brown', 'black', 'green', 'red', 'gray'
            consistency: Poo consistency - 'solid', 'loose', 'runny', 'mucousy', 'hard', 'pebbles', 'diarrhea'
            diaper_rash: Whether baby has diaper rash
            notes: Optional notes about this diaper change
        """
        await self._log_diaper_or_potty_event(
            child_uid,
            mode,
            pref_field="lastDiaper",
            pee_amount=pee_amount,
            poo_amount=poo_amount,
            color=color,
            consistency=consistency,
            diaper_rash=diaper_rash,
            notes=notes,
            start_time=start_time,
        )

    async def log_potty(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        mode: DiaperMode,
        how_it_happened: PottyResult,
        pee_amount: Literal["little", "medium", "big"] | None = None,
        poo_amount: Literal["little", "medium", "big"] | None = None,
        color: PooColor | None = None,
        consistency: PooConsistency | None = None,
        notes: str | None = None,
    ) -> None:
        """Log a potty event in the shared diaper tracker.

        Args:
            child_uid: Child unique identifier
            start_time: Event time
            mode: One of 'pee', 'poo', 'both', 'dry'
            how_it_happened: One of 'satButDry', 'wentPotty', or 'accident'
            pee_amount: Pee amount - 'little', 'medium', 'big', or None (no quantity)
            poo_amount: Poo amount - 'little', 'medium', 'big', or None (no quantity)
            color: Poo color - 'yellow', 'brown', 'black', 'green', 'red', 'gray'
            consistency: Poo consistency - 'solid', 'loose', 'runny', 'mucousy', 'hard', 'pebbles', 'diarrhea'
            notes: Optional notes about this potty event
        """
        await self._log_diaper_or_potty_event(
            child_uid,
            mode,
            pref_field="lastPotty",
            pee_amount=pee_amount,
            poo_amount=poo_amount,
            color=color,
            consistency=consistency,
            notes=notes,
            is_potty=True,
            how_it_happened=how_it_happened,
            start_time=start_time,
        )

    async def log_growth(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        weight: float | None = None,
        height: float | None = None,
        head: float | None = None,
        units: Literal["metric", "imperial"] = "metric",
    ) -> None:
        """
        Log growth measurements (weight, height, head circumference).

        Args:
            child_uid: Child unique identifier
            start_time: Event time
            weight: Weight measurement (kg for metric, lbs for imperial)
            height: Height measurement (cm for metric, inches for imperial)
            head: Head circumference (cm for metric, inches for imperial)
            units: 'metric' or 'imperial'
        """
        _LOGGER.info("Logging growth data for child %s", child_uid)

        if not any([weight, height, head]):
            raise ValueError("At least one measurement (weight, height, or head) is required")

        client = await self._get_firestore_client()
        health_ref = client.collection("health").document(child_uid)

        start_timestamp = start_time.timestamp()
        current_time = time.time()
        current_offset = await self._get_timezone_offset_minutes()
        health_doc = await health_ref.get()
        health_model = FirebaseHealthDocumentData.model_validate(health_doc.to_dict() or {})
        existing_last_growth = health_model.prefs.lastGrowthEntry if health_model.prefs else None
        existing_last_growth_start = existing_last_growth.start if existing_last_growth else None
        should_update_last_growth = existing_last_growth_start is None or start_timestamp >= float(
            existing_last_growth_start
        )

        # Create interval ID (timestamp in ms + random suffix)
        interval_timestamp_ms = int(current_time * 1000)
        interval_id = f"{interval_timestamp_ms}-{uuid.uuid4().hex[:20]}"

        # Build growth entry matching Huckleberry app structure
        growth_entry = FirebaseGrowthData(
            id_=interval_id,
            type="health",
            mode="growth",
            start=start_timestamp,
            lastUpdated=current_time,
            offset=current_offset,
            isNight=False,
            multientry_key=None,
        )

        # Add measurements with proper unit fields (matches app structure)
        if units == "metric":
            if weight is not None:
                growth_entry.weight = float(weight)
                growth_entry.weightUnits = "kg"
            if height is not None:
                growth_entry.height = float(height)
                growth_entry.heightUnits = "cm"
            if head is not None:
                growth_entry.head = float(head)
                growth_entry.headUnits = "hcm"
        else:  # imperial
            if weight is not None:
                growth_entry.weight = float(weight)
                growth_entry.weightUnits = "lbs.oz"
            if height is not None:
                growth_entry.height = float(height)
                growth_entry.heightUnits = "ft.in"
            if head is not None:
                growth_entry.head = float(head)
                growth_entry.headUnits = "hin"

        # Create interval document in health/{child_uid}/data subcollection
        # (Health uses "data" subcollection, not "intervals" like other trackers)
        health_data_ref = health_ref.collection("data").document(interval_id)

        try:
            await health_data_ref.set(to_firebase_dict(growth_entry))
            _LOGGER.info("Created growth data entry in subcollection: %s", interval_id)
        except GoogleAPICallError as err:
            _LOGGER.error("Failed to create growth data entry: %s", err)
            # Continue to update prefs even if subcollection write fails

        # Update prefs.lastGrowthEntry and timestamps (matches Huckleberry app structure)
        if should_update_last_growth:
            try:
                await health_ref.update(
                    {
                        "prefs.lastGrowthEntry": to_firebase_dict(growth_entry),
                        "prefs.timestamp": {"seconds": current_time},
                        "prefs.local_timestamp": current_time,
                    }
                )
            except GoogleAPICallError as err:
                _LOGGER.error("Failed to log growth data: %s", err)
                raise

        _LOGGER.info("Growth data logged successfully (updated_last=%s)", should_update_last_growth)

    async def log_pump(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        duration: float | int | None = None,
        left_amount: float | int | None = None,
        right_amount: float | int | None = None,
        total_amount: float | int | None = None,
        units: VolumeUnits = "ml",
        notes: str | None = None,
    ) -> None:
        """Log a pump entry.

        Args:
            child_uid: Child unique identifier.
            start_time: Session start in datetime.
            duration: Optional session duration in seconds.
            left_amount: Left-side amount for `leftright` entries. Required with `right_amount`.
            right_amount: Right-side amount for `leftright` entries. Required with `left_amount`.
            total_amount: Total amount for `total` entries. Stored split evenly across both side fields.
            units: Volume units ("ml" or "oz").
            notes: Optional notes attached to the interval.
        """
        if duration is not None and float(duration) < 0:
            raise ValueError("duration must be non-negative")

        using_total_amount = total_amount is not None
        if using_total_amount and (left_amount is not None or right_amount is not None):
            raise ValueError("Provide either total_amount or left/right amounts, not both")

        if using_total_amount:
            assert total_amount is not None
            resolved_entry_mode: PumpEntryMode = "total"
            per_side_amount = float(total_amount) / 2.0
            resolved_left_amount = per_side_amount
            resolved_right_amount = per_side_amount
        else:
            resolved_entry_mode = "leftright"
            if left_amount is None or right_amount is None:
                raise ValueError("leftright pump entries require both left_amount and right_amount")
            resolved_left_amount = float(left_amount)
            resolved_right_amount = float(right_amount)

        start_timestamp = start_time.timestamp()
        current_offset = await self._get_timezone_offset_minutes()
        end_offset = current_offset if duration is not None else None

        current_time = time.time()
        interval_id = f"{int(current_time * 1000)}-{uuid.uuid4().hex[:20]}"
        interval = FirebasePumpIntervalData(
            start=start_timestamp,
            entryMode=resolved_entry_mode,
            leftAmount=resolved_left_amount,
            rightAmount=resolved_right_amount,
            units=units,
            offset=current_offset,
            duration=float(duration) if duration is not None else None,
            end_offset=end_offset,
            lastUpdated=current_time,
            notes=notes,
        )

        last_pump = FirebaseLastPumpData(
            start=start_timestamp,
            duration=float(duration) if duration is not None else None,
            entryMode=resolved_entry_mode,
            leftAmount=resolved_left_amount,
            rightAmount=resolved_right_amount,
            units=units,
            offset=current_offset,
        )

        client = await self._get_firestore_client()
        pump_ref = client.collection("pump").document(child_uid)
        pump_doc = await pump_ref.get()
        await pump_ref.collection("intervals").document(interval_id).set(to_firebase_dict(interval))

        pump_model = FirebasePumpDocumentData.model_validate(pump_doc.to_dict() or {})
        existing_last_pump = pump_model.prefs.lastPump if pump_model.prefs else None
        existing_start = existing_last_pump.start if existing_last_pump else None
        should_update_last_pump = True
        if existing_start is not None and start_timestamp < float(existing_start):
            should_update_last_pump = False

        if should_update_last_pump:
            await pump_ref.update(
                {
                    "prefs.lastPump": to_firebase_dict(last_pump),
                    "prefs.timestamp": {"seconds": current_time},
                    "prefs.local_timestamp": current_time,
                }
            )

        _LOGGER.info(
            "Pump logged for child %s with mode %s (updated_last=%s)",
            child_uid,
            resolved_entry_mode,
            should_update_last_pump,
        )

    async def log_activity(
        self,
        child_uid: str,
        *,
        mode: ActivityMode,
        start_time: datetime,
        duration: float | int | None = None,
        notes: str | None = None,
    ) -> None:
        """Log an activity entry.

        Args:
            child_uid: Child unique identifier.
            mode: Activity mode.
            start_time: Activity start in datetime.
            duration: Optional activity duration in seconds.
            notes: Optional notes attached to the interval.
        """
        if duration is not None and float(duration) < 0:
            raise ValueError("duration must be non-negative")

        start_timestamp = start_time.timestamp()
        current_offset = await self._get_timezone_offset_minutes()
        end_offset = current_offset if duration is not None else None
        current_time = time.time()
        interval_id = f"{int(current_time * 1000)}-{uuid.uuid4().hex[:20]}"

        interval = FirebaseActivityIntervalData(
            mode=mode,
            start=start_timestamp,
            offset=current_offset,
            duration=float(duration) if duration is not None else None,
            end_offset=end_offset,
            lastUpdated=current_time,
            notes=notes,
        )

        last_activity = FirebaseLastActivityData(
            start=start_timestamp,
            offset=current_offset,
            duration=float(duration) if duration is not None else None,
            end_offset=end_offset,
        )

        client = await self._get_firestore_client()
        activities_ref = client.collection("activities").document(child_uid)
        activities_doc = await activities_ref.get()
        await activities_ref.collection("intervals").document(interval_id).set(to_firebase_dict(interval))

        activities_model = FirebaseActivityDocumentData.model_validate(activities_doc.to_dict() or {})
        last_field_name = _ACTIVITY_LAST_FIELD_BY_MODE[mode]
        existing_last_activity = (
            getattr(activities_model.prefs, last_field_name, None) if activities_model.prefs else None
        )
        existing_start = existing_last_activity.start if existing_last_activity else None
        should_update_last_activity = True
        if existing_start is not None and start_timestamp < float(existing_start):
            should_update_last_activity = False

        if should_update_last_activity:
            await activities_ref.update(
                {
                    f"prefs.{last_field_name}": to_firebase_dict(last_activity),
                    "prefs.timestamp": {"seconds": current_time},
                    "prefs.local_timestamp": current_time,
                }
            )

        _LOGGER.info(
            "Activity logged for child %s with mode %s (updated_last=%s)",
            child_uid,
            mode,
            should_update_last_activity,
        )

    async def get_latest_growth(self, child_uid: str) -> FirebaseGrowthData | None:
        """
        Get the latest growth measurements for a child.

        Args:
            child_uid: Child unique identifier

        Returns:
            Latest Firebase growth entry, if present
        """
        client = await self._get_firestore_client()
        health_ref = client.collection("health").document(child_uid)

        try:
            doc = await health_ref.get()
            if not doc.exists:
                return None

            health_data = doc.to_dict()
            if not health_data:
                return None

            health_model = FirebaseHealthDocumentData.model_validate(health_data)
            last_growth = health_model.prefs.lastGrowthEntry if health_model.prefs else None

            if not last_growth:
                return None

            return FirebaseGrowthData.model_validate(last_growth.model_dump(by_alias=True, exclude_none=True))
        except (GoogleAPICallError, ValidationError, RuntimeError, TypeError, ValueError) as err:
            _LOGGER.error("Failed to get growth data: %s", err)
            return None

    async def list_sleep_intervals(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FirebaseSleepIntervalData]:
        """
        Fetch sleep intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated sleep interval entries
        """
        events: list[FirebaseSleepIntervalData] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)
        intervals_ref = sleep_ref.collection("intervals")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = (
                intervals_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                interval = FirebaseSleepIntervalData.model_validate(data)
                events.append(interval)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = intervals_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebaseSleepMultiContainer.model_validate(data)

                # Iterate through batched entries and filter by date
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching sleep intervals: %s", err)

        return events

    async def list_feed_intervals(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FirebaseFeedIntervalData]:
        """
        Fetch feeding intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated feed interval entries
        """
        events: list[FirebaseFeedIntervalData] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)
        intervals_ref = feed_ref.collection("intervals")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = (
                intervals_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                interval = _FEED_INTERVAL_ADAPTER.validate_python(data)
                feed_mode = getattr(interval, "mode", None)
                if feed_mode is None:
                    continue

                events.append(interval)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = intervals_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebaseFeedMultiContainer.model_validate(data)

                # Iterate through batched entries and filter by date
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    feed_mode = getattr(entry, "mode", None)
                    if feed_mode is None:
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching feed intervals: %s", err)

        return events

    async def list_diaper_intervals(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FirebaseDiaperData]:
        """
        Fetch diaper intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated diaper interval entries
        """
        events: list[FirebaseDiaperData] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        diaper_ref = client.collection("diaper").document(child_uid)
        intervals_ref = diaper_ref.collection("intervals")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = (
                intervals_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                entry = FirebaseDiaperData.model_validate(data)
                events.append(entry)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = intervals_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebaseDiaperMultiContainer.model_validate(data)

                # Iterate through batched entries and filter by date
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching diaper intervals: %s", err)

        return events

    async def list_health_entries(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HealthDataEntry]:
        """
        Fetch health entries from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated health entries
        """
        events: list[HealthDataEntry] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        health_ref = client.collection("health").document(child_uid)
        # Health uses "data" subcollection, not "intervals"
        data_ref = health_ref.collection("data")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = (
                data_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                entry = _HEALTH_ENTRY_ADAPTER.validate_python(data)
                events.append(entry)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = data_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebaseHealthMultiContainer.model_validate(data)

                # Iterate through batched entries and filter by date
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching health entries: %s", err)

        return events

    async def list_pump_intervals(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FirebasePumpIntervalData]:
        """
        Fetch pump intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated pump interval entries
        """
        events: list[FirebasePumpIntervalData] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        pump_ref = client.collection("pump").document(child_uid)
        intervals_ref = pump_ref.collection("intervals")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = (
                intervals_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                entry = FirebasePumpIntervalData.model_validate(data)
                events.append(entry)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = intervals_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebasePumpMultiContainer.model_validate(data)

                # Iterate through batched entries and filter by date
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching pump intervals: %s", err)

        return events

    async def list_activity_intervals(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FirebaseActivityIntervalData]:
        """
        Fetch activity intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated activity interval entries
        """
        events: list[FirebaseActivityIntervalData] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        activities_ref = client.collection("activities").document(child_uid)
        intervals_ref = activities_ref.collection("intervals")

        try:
            regular_docs = (
                intervals_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue

                entry = FirebaseActivityIntervalData.model_validate(data)
                events.append(entry)

            multi_docs = intervals_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebaseActivityMultiContainer.model_validate(data)
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching activity intervals: %s", err)

        return events
