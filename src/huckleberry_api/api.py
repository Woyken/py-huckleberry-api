"""API client for Huckleberry."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Callable, Literal, TypeVar, cast

import requests
from google.auth.credentials import Credentials
from google.cloud import firestore

from .const import AUTH_URL, FIREBASE_API_KEY, REFRESH_URL
from .types import (
    ChildData,
    DiaperDocumentData,
    FeedDocumentData,
    FirebaseDiaperInterval,
    FirebaseFeedDocument,
    FirebaseGrowthData,
    FirebaseSleepDocument,
    GrowthData,
    HealthDocumentData,
    LastDiaperData,
    LastNursingData,
    LastSideData,
    LastSleepData,
    SleepDocumentData,
)

# Type aliases for known string values
CollectionName = Literal["sleep", "feed", "health", "diaper"]
FeedSide = Literal["left", "right"]
DiaperMode = Literal["pee", "poo", "both", "dry"]
DiaperAmount = Literal["little", "medium", "big"]
PooColor = Literal["yellow", "brown", "black", "green", "red", "gray"]
PooConsistency = Literal["solid", "loose", "runny", "mucousy", "hard", "pebbles", "diarrhea"]
MeasurementUnits = Literal["metric", "imperial"]

# Union type for all document data types used in listeners
DocumentData = SleepDocumentData | FeedDocumentData | HealthDocumentData | DiaperDocumentData
TDocumentData = TypeVar('TDocumentData', SleepDocumentData, FeedDocumentData, HealthDocumentData, DiaperDocumentData)

_LOGGER = logging.getLogger(__name__)


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
        Token refreshing is managed externally by HuckleberryAPI.refresh_auth_token(),
        and a new FirebaseTokenCredentials instance is created with the refreshed token.
        """


class HuckleberryAPI:
    """API client for Huckleberry."""

    def __init__(self, email: str, password: str) -> None:
        """Initialize the API client."""
        self.email = email
        self.password = password
        self.id_token: str | None = None
        self.refresh_token: str | None = None
        self.user_uid: str | None = None
        self.token_expires_at: float | None = None
        self._firestore_client: firestore.Client | None = None
        self._listeners: dict = {}  # Store active listeners
        self._listener_callbacks: dict = {}  # Store callbacks to recreate listeners

    def authenticate(self) -> None:
        """Authenticate with Firebase."""
        _LOGGER.debug("Authenticating with Huckleberry")

        try:
            response = requests.post(
                f"{AUTH_URL}?key={FIREBASE_API_KEY}",
                json={
                    "email": self.email,
                    "password": self.password,
                    "returnSecureToken": True,
                },
                timeout=10,
            )
            response.raise_for_status()

            data = response.json()
            self.id_token = data["idToken"]
            self.refresh_token = data["refreshToken"]
            self.user_uid = data["localId"]
            self.token_expires_at = datetime.now().timestamp() + int(data["expiresIn"])

            _LOGGER.info("Successfully authenticated with Huckleberry")
        except requests.exceptions.HTTPError as err:
            _LOGGER.error("Authentication failed: %s", err)
            if err.response is not None:
                try:
                    error_data = err.response.json()
                    error_message = error_data.get("error", {}).get("message", "Unknown error")
                    _LOGGER.error("Firebase error: %s", error_message)
                except Exception:
                    _LOGGER.error("Response: %s", err.response.text)
            raise

    def maintain_session(self) -> None:
        """Ensure the session is valid and refresh token if needed.

        This should be called periodically (e.g. by coordinator) to ensure
        listeners don't die due to token expiration.
        """
        self._ensure_authenticated()

    def refresh_auth_token(self) -> None:
        """Refresh the authentication token."""
        if not self.refresh_token:
            raise ValueError("No refresh token available")

        _LOGGER.debug("Refreshing authentication token")

        response = requests.post(
            f"{REFRESH_URL}?key={FIREBASE_API_KEY}",
            json={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=10,
        )
        response.raise_for_status()

        data = response.json()
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
            except Exception as err:
                _LOGGER.error("Error stopping listener %s before refresh: %s", key, err)
        self._listeners.clear()

        # Invalidate the Firestore client so it gets recreated with new token
        self._firestore_client = None

        _LOGGER.debug("Successfully refreshed authentication token")

        # Recreate all listeners with new token
        _LOGGER.info("Recreating %d listeners with refreshed token", len(self._listener_callbacks))
        callbacks_copy = dict(self._listener_callbacks)  # Copy to avoid modification during iteration
        for key, (listener_type, child_uid, callback) in callbacks_copy.items():
            try:
                if listener_type == "sleep":
                    self.setup_realtime_listener(child_uid, callback)
                elif listener_type == "feed":
                    self.setup_feed_listener(child_uid, callback)
                elif listener_type == "health":
                    self.setup_health_listener(child_uid, callback)
                elif listener_type == "diaper":
                    self.setup_diaper_listener(child_uid, callback)
                _LOGGER.debug("Recreated %s listener for child %s", listener_type, child_uid)
            except Exception as err:
                _LOGGER.error("Error recreating %s listener for child %s: %s", listener_type, child_uid, err)

    def _ensure_authenticated(self) -> None:
        """Ensure we have a valid authentication token."""
        if not self.id_token:
            self.authenticate()
        elif self.token_expires_at and datetime.now().timestamp() >= self.token_expires_at - 300:
            # Refresh if token expires in less than 5 minutes
            self.refresh_auth_token()

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        self._ensure_authenticated()
        return {
            "Authorization": f"Bearer {self.id_token}",
            "Content-Type": "application/json",
        }

    def _get_firestore_client(self) -> firestore.Client:
        """Get or create Firestore client."""
        self._ensure_authenticated()

        # Create new client if token changed or client doesn't exist
        if not self._firestore_client:
            assert self.id_token is not None, "id_token should be set after authentication"
            credentials = FirebaseTokenCredentials(self.id_token)
            self._firestore_client = firestore.Client(
                project="simpleintervals",
                credentials=credentials,
            )

        return self._firestore_client

    def get_children(self) -> list[ChildData]:
        """Get list of children from user profile."""
        _LOGGER.debug("Fetching children list")

        try:
            # Get Firestore client
            db = self._get_firestore_client()

            # Get user document which contains lastChild reference
            user_ref = db.collection("users").document(self.user_uid)
            user_doc = user_ref.get()

            if not user_doc.exists:
                _LOGGER.error("User document not found")
                return []

            user_data = user_doc.to_dict()
            if not user_data:
                _LOGGER.error("User document has no data")
                return []

            child_list = user_data.get("childList")
            if not child_list:
                _LOGGER.error("No childList found in user document")
                return []

            children = []
            for child in user_data.get("childList"):
                child_id = child.get("cid")

                if not child_id:
                    _LOGGER.warning("Child id not found in childList")
                    return []

                # Get child document
                child_ref = db.collection("childs").document(child_id)
                child_doc = child_ref.get()

                if not child_doc.exists:
                    _LOGGER.error("Child document not found: %s", child_id)
                    return []

                child_data = child_doc.to_dict()
                if not child_data:
                    _LOGGER.error("Child document has no data: %s", child_id)
                    return []

                # Name may appear as 'childsName' in some documents
                display_name = child_data.get("name") or child_data.get("childsName") or "Unknown"

                child: ChildData = {
                    "uid": child_id,
                    "name": display_name,
                    "birthday": child_data.get("birthdate"),
                    "picture": child_data.get("picture"),
                    "gender": child_data.get("gender"),
                    "color": child_data.get("color"),
                    "created_at": child_data.get("createdAt"),
                    "night_start_min": child_data.get("nightStart"),
                    "morning_cutoff_min": child_data.get("morningCutoff"),
                    "expected_naps": child_data.get("naps"),
                    "categories": child_data.get("categories"),
                }

                children.append(child)

            _LOGGER.info("Found %d children", len(children))
            return children

        except Exception as err:
            _LOGGER.error("Failed to get children: %s", err)
            raise

    def start_sleep(self, child_uid: str) -> None:
        """Start sleep tracking for a child."""
        _LOGGER.info("Starting sleep tracking for child %s", child_uid)

        client = self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)

        current_time = time.time()
        current_time_ms = current_time * 1000  # Milliseconds for timerStartTime

        # Generate a unique session UUID (16 hex characters like the app)
        session_uuid = uuid.uuid4().hex[:16]

        # Update the timer field to mark sleep as active
        # Match the structure from the Huckleberry app
        sleep_data: FirebaseSleepDocument = {
            "timer": {
                "active": True,
                "paused": False,
                "timestamp": {"seconds": current_time},
                "local_timestamp": current_time,
                "timerStartTime": current_time_ms,  # Milliseconds timestamp
                "uuid": session_uuid,  # Unique session identifier
                "details": {
                    "startSleepCondition": {
                        "happy": False,
                        "longTimeToFallAsleep": False,
                        "10-20_minutes": False,
                        "upset": False,
                        "under_10_minutes": False,
                    },
                    "sleepLocations": {
                        "car": False,
                        "nursing": False,
                        "wornOrHeld": False,
                        "stroller": False,
                        "coSleep": False,
                        "nextToCarer": False,
                        "onOwnInBed": False,
                        "bottle": False,
                        "swing": False,
                    },
                    "endSleepCondition": {
                        "happy": False,
                        "wokeUpChild": False,
                        "upset": False,
                    },
                },
            }
        }
        sleep_ref.set(cast(dict, sleep_data), merge=True)

        _LOGGER.info("Sleep tracking started successfully")

    def pause_sleep(self, child_uid: str) -> None:
        """Pause current sleep session without ending it."""
        _LOGGER.info("Pausing sleep for child %s", child_uid)

        client = self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)

        # Check if timer is active
        sleep_doc = sleep_ref.get(timeout=10.0)
        if not sleep_doc.exists:
            _LOGGER.warning("No sleep document to pause for %s", child_uid)
            return

        timer = sleep_doc.to_dict().get("timer", {})
        if not timer.get("active", False):
            _LOGGER.info("Sleep is not active for %s, ignoring pause request", child_uid)
            return

        if timer.get("paused", False):
            _LOGGER.info("Sleep is already paused for %s", child_uid)
            return

        now = time.time()
        timer_end_time_ms = now * 1000  # Convert to milliseconds

        # Add timerEndTime field that app uses to show end time when paused
        sleep_ref.update({
            "timer.paused": True,
            "timer.active": True,
            "timer.timerEndTime": timer_end_time_ms,
            "timer.timestamp": {"seconds": now},
            "timer.local_timestamp": now,
        })

        _LOGGER.info("Sleep paused for child %s", child_uid)

    def resume_sleep(self, child_uid: str) -> None:
        """Resume a paused sleep session."""
        _LOGGER.info("Resuming sleep for child %s", child_uid)

        client = self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)

        # Check if timer is active and paused
        sleep_doc = sleep_ref.get(timeout=10.0)
        if not sleep_doc.exists:
            _LOGGER.warning("No sleep document to resume for %s", child_uid)
            return

        timer = sleep_doc.to_dict().get("timer", {})
        if not timer.get("active", False):
            _LOGGER.info("Sleep is not active for %s, ignoring resume request", child_uid)
            return

        if not timer.get("paused", False):
            _LOGGER.info("Sleep is not paused for %s, ignoring resume request", child_uid)
            return

        now = time.time()
        sleep_ref.update({
            "timer.paused": False,
            "timer.active": True,
            "timer.timestamp": {"seconds": now},
            "timer.local_timestamp": now,
        })

        _LOGGER.info("Sleep resumed for child %s", child_uid)

    def cancel_sleep(self, child_uid: str) -> None:
        """Cancel current sleep session without saving an interval."""
        _LOGGER.info("Cancelling current sleep for child %s", child_uid)

        client = self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)

        # Check current state
        doc = sleep_ref.get(timeout=10.0)
        if doc.exists:
            timer_data = doc.to_dict()
            if timer_data:
                timer = timer_data.get("timer", {})
                _LOGGER.info("Current timer state: active=%s, paused=%s", timer.get("active"), timer.get("paused"))
                session_uuid = timer.get("uuid", uuid.uuid4().hex[:16])
            else:
                session_uuid = uuid.uuid4().hex[:16]
        else:
            _LOGGER.warning("Sleep document does not exist for child %s", child_uid)
            session_uuid = uuid.uuid4().hex[:16]

        # Set timer to inactive (don't delete it - app expects it to remain)
        current_time = time.time()
        sleep_ref.update({
            "timer": {
                "active": False,
                "paused": False,
                "timestamp": {"seconds": current_time},
                "timerStartTime": None,
                "uuid": session_uuid,
                "local_timestamp": current_time,
            },
        })

        _LOGGER.info("Sleep cancelled for child %s", child_uid)

    def complete_sleep(self, child_uid: str) -> None:
        """Complete current sleep session and save interval."""
        _LOGGER.info("Completing sleep for child %s", child_uid)

        client = self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)

        sleep_doc = sleep_ref.get(timeout=10.0)
        if not sleep_doc.exists:
            _LOGGER.warning("No active sleep document to complete for %s", child_uid)
            return

        data = sleep_doc.to_dict() or {}
        timer = data.get("timer") or {}

        # Check if timer is already inactive (already completed)
        if not timer.get("active", False):
            _LOGGER.info("Sleep already completed for %s, ignoring duplicate request", child_uid)
            return

        timer_start_ms = timer.get("timerStartTime")
        if not timer_start_ms:
            # Attempt fallback: reconstruct using timestamp.seconds if available
            ts_seconds = timer.get("timestamp", {}).get("seconds")
            if ts_seconds:
                timer_start_ms = int(float(ts_seconds) * 1000)
                _LOGGER.warning("timerStartTime missing; falling back to timestamp.seconds for %s", child_uid)
            else:
                _LOGGER.warning("Missing timerStartTime; cannot compute duration for %s", child_uid)
                sleep_ref.update({"timer": firestore.DELETE_FIELD})
                return

        now_ms = time.time() * 1000

        # If sleep is paused, use timerEndTime as the end time (not current time)
        if timer.get("paused", False) and "timerEndTime" in timer:
            end_ms = timer["timerEndTime"]
            _LOGGER.info("Sleep is paused, using timerEndTime for completion")
        else:
            end_ms = now_ms

        duration_sec = int((end_ms - float(timer_start_ms)) / 1000)
        start_sec = int(float(timer_start_ms) / 1000)

        intervals_ref = sleep_ref.collection("intervals")
        interval_id = uuid.uuid4().hex[:16]
        intervals_ref.document(interval_id).set({
            "_id": interval_id,
            "start": start_sec,
            "duration": duration_sec,
            "offset": -120.0,
            "end_offset": -120.0,
            "details": timer.get("details", {}),
            "lastUpdated": time.time(),
        })

        # Set timer to inactive (match stop_sleep behavior)
        current_time = time.time()
        session_uuid = timer.get("uuid", uuid.uuid4().hex[:16])

        last_sleep_data: LastSleepData = {
            "start": start_sec,
            "duration": duration_sec,
            "offset": -120.0,
        }

        sleep_ref.update({
            "timer": {
                "active": False,
                "paused": False,
                "timestamp": {"seconds": current_time},
                "timerStartTime": None,
                "uuid": session_uuid,
                "local_timestamp": current_time,
            },
            "prefs.lastSleep": last_sleep_data,
            "prefs.timestamp": {"seconds": current_time},
            "prefs.local_timestamp": current_time,
        })

        _LOGGER.info("Sleep completed for child %s (duration %ss)", child_uid, duration_sec)

    def start_feeding(self, child_uid: str, side: FeedSide = "left") -> None:
        """Start feeding tracking."""
        _LOGGER.info("Starting feeding for child %s on %s side", child_uid, side)

        client = self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        current_time = time.time()

        session_uuid = uuid.uuid4().hex[:16]

        feed_data: FirebaseFeedDocument = {
            "timer": {
                "active": True,
                "paused": False,
                "timestamp": {"seconds": current_time},
                "local_timestamp": current_time,
                "feedStartTime": current_time,
                "timerStartTime": current_time,
                "uuid": session_uuid,
                "leftDuration": 0.0,
                "rightDuration": 0.0,
                "lastSide": "left",  # Always start with lastSide as left
                "activeSide": side,  # activeSide indicates which side is currently feeding
            }
        }
        feed_ref.set(cast(dict, feed_data), merge=True)

        _LOGGER.info("Feeding started on %s side", side)

    def pause_feeding(self, child_uid: str) -> None:
        """Pause current feeding session."""
        _LOGGER.info("Pausing feeding for child %s", child_uid)

        client = self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        doc = feed_ref.get(timeout=10.0)
        if not doc.exists:
            _LOGGER.warning("Feed document not found")
            return

        timer_data = doc.to_dict()
        if not timer_data:
            _LOGGER.warning("Feed document has no data")
            return

        timer = timer_data.get("timer", {})

        if not timer.get("active", False):
            _LOGGER.info("Feeding is not active for %s, ignoring pause request", child_uid)
            return

        if timer.get("paused", False):
            _LOGGER.info("Feeding is already paused for %s", child_uid)
            return
        current_side = timer.get("activeSide", timer.get("lastSide", "left"))

        # Calculate elapsed time and accumulate to current side
        now = time.time()
        timer_start = timer.get("timerStartTime", now)
        elapsed = now - timer_start

        left_duration = timer.get("leftDuration", 0.0)
        right_duration = timer.get("rightDuration", 0.0)

        if current_side == "left":
            left_duration += elapsed
        else:
            right_duration += elapsed

        feed_ref.update({
            "timer.paused": True,
            "timer.active": True,
            "timer.timestamp": {"seconds": now},
            "timer.local_timestamp": now,
            "timer.leftDuration": left_duration,
            "timer.rightDuration": right_duration,
            "timer.lastSide": current_side,
        })

        # Remove activeSide when paused
        from google.cloud.firestore import DELETE_FIELD
        feed_ref.update({"timer.activeSide": DELETE_FIELD})

        _LOGGER.info("Feeding paused (L:%ss R:%ss)", left_duration, right_duration)

    def resume_feeding(self, child_uid: str, side: FeedSide | None = None) -> None:
        """Resume paused feeding session."""
        _LOGGER.info("Resuming feeding for child %s", child_uid)

        client = self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        doc = feed_ref.get(timeout=10.0)
        if not doc.exists:
            _LOGGER.warning("Feed document not found")
            return

        timer_data = doc.to_dict()
        if not timer_data:
            _LOGGER.warning("Feed document has no data")
            return

        timer = timer_data.get("timer", {})

        if not timer.get("active", False):
            _LOGGER.info("Feeding is not active for %s, ignoring resume request", child_uid)
            return

        if not timer.get("paused", False):
            _LOGGER.info("Feeding is not paused for %s, ignoring resume request", child_uid)
            return
        if side is None:
            side = timer.get("lastSide", "left")

        now = time.time()

        feed_ref.update({
            "timer.paused": False,
            "timer.active": True,
            "timer.timestamp": {"seconds": now},
            "timer.local_timestamp": now,
            "timer.timerStartTime": now,  # Reset timer start time on resume
            "timer.activeSide": side,
            "timer.lastSide": "none",  # Set to none during transition
        })

        _LOGGER.info("Feeding resumed on %s", side)

    def switch_feeding_side(self, child_uid: str) -> None:
        """Switch feeding side (left <-> right)."""
        _LOGGER.info("Switching feeding side for child %s", child_uid)

        client = self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        doc = feed_ref.get(timeout=10.0)
        if not doc.exists:
            _LOGGER.warning("Feed document not found")
            return

        timer_data = doc.to_dict()
        if not timer_data:
            _LOGGER.warning("Feed document has no data")
            return

        timer = timer_data.get("timer", {})

        if not timer.get("active", False):
            _LOGGER.info("Feeding is not active for %s, ignoring switch request", child_uid)
            return
        current_side = timer.get("activeSide", timer.get("lastSide", "left"))
        new_side = "right" if current_side == "left" else "left"
        is_paused = timer.get("paused", False)

        now = time.time()
        left_duration = timer.get("leftDuration", 0.0)
        right_duration = timer.get("rightDuration", 0.0)

        # Only accumulate duration if NOT paused
        if not is_paused:
            # Calculate duration since timer started and accumulate to current side
            timer_start = timer.get("timerStartTime", now)
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

        feed_ref.update(update_data)

        _LOGGER.info("Switched from %s to %s (L:%ss R:%ss)", current_side, new_side, left_duration, right_duration)

    def cancel_feeding(self, child_uid: str) -> None:
        """Cancel current feeding without saving."""
        _LOGGER.info("Cancelling feeding for child %s", child_uid)

        client = self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        doc = feed_ref.get(timeout=10.0)
        if doc.exists:
            timer_data = doc.to_dict()
            if timer_data:
                timer = timer_data.get("timer", {})
                session_uuid = timer.get("uuid", uuid.uuid4().hex[:16])
            else:
                session_uuid = uuid.uuid4().hex[:16]
        else:
            session_uuid = uuid.uuid4().hex[:16]

        current_time = time.time()
        feed_ref.update({
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
        })

        _LOGGER.info("Feeding cancelled")

    def complete_feeding(self, child_uid: str) -> None:
        """Complete current feeding and save to history."""
        _LOGGER.info("Completing feeding for child %s", child_uid)

        client = self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        doc = feed_ref.get(timeout=10.0)
        if not doc.exists:
            _LOGGER.warning("No active feed document to complete")
            return

        data = doc.to_dict() or {}
        timer = data.get("timer") or {}

        # Check if timer is already inactive (already completed)
        if not timer.get("active", False):
            _LOGGER.info("Feeding already completed for %s, ignoring duplicate request", child_uid)
            return

        timer_start = timer.get("timerStartTime")
        if not timer_start:
            _LOGGER.warning("Missing timerStartTime for feeding")
            return

        now_time = time.time()
        # timerStartTime is in seconds for feeding
        timer_start_sec = float(timer_start)

        left_duration = timer.get("leftDuration", 0.0)
        right_duration = timer.get("rightDuration", 0.0)

        # Add elapsed time on current side if not paused
        if not timer.get("paused", False):
            elapsed = now_time - timer_start_sec
            current_side = timer.get("activeSide", timer.get("lastSide", "left"))

            if current_side == "left":
                left_duration += elapsed
            else:
                right_duration += elapsed

        # Calculate total duration from accumulated durations
        total_duration = left_duration + right_duration

        session_uuid = timer.get("uuid", uuid.uuid4().hex[:16])
        feed_start_time = timer.get("feedStartTime", timer_start_sec)

        # Determine last side for history
        last_side_value = timer.get("activeSide", timer.get("lastSide", "right"))
        if last_side_value == "none":
            last_side_value = "right" if right_duration >= left_duration else "left"

        from google.cloud.firestore import DELETE_FIELD

        # Create interval document ID (format: timestamp-random)
        interval_id = f"{int(now_time * 1000)}-{uuid.uuid4().hex[:20]}"

        # Create interval document for history (feed/{child_uid}/intervals)
        feed_intervals_ref = feed_ref.collection("intervals").document(interval_id)

        try:
            feed_intervals_ref.set({
                "mode": "breast",
                "start": feed_start_time,
                "lastSide": last_side_value,
                "lastUpdated": now_time,
                "leftDuration": left_duration,
                "rightDuration": right_duration,
                "offset": -120.0,
                "end_offset": -120.0,
            })
            _LOGGER.info("Created feeding interval entry: %s", interval_id)
        except Exception as err:
            _LOGGER.error("Failed to create feeding interval entry: %s", err)

        last_nursing_data: LastNursingData = {
            "mode": "breast",
            "start": feed_start_time,
            "duration": total_duration,
            "leftDuration": left_duration,
            "rightDuration": right_duration,
            "offset": -120.0,
        }

        last_side_data: LastSideData = {
            "start": feed_start_time,
            "lastSide": last_side_value,
        }

        # Update to inactive and save to lastNursing
        feed_ref.update({
            "timer.active": False,
            "timer.paused": True,
            "timer.timestamp": {"seconds": now_time},
            "timer.local_timestamp": now_time,
            "timer.lastSide": last_side_value,
            "timer.leftDuration": DELETE_FIELD,  # Remove durations from timer
            "timer.rightDuration": DELETE_FIELD,
            "timer.activeSide": DELETE_FIELD,  # Remove activeSide
            "prefs.lastNursing": last_nursing_data,
            "prefs.lastSide": last_side_data,
            "prefs.timestamp": {"seconds": now_time},
            "prefs.local_timestamp": now_time,
        })

        _LOGGER.info("Feeding completed (total duration %ss, L:%ss R:%ss)", total_duration, left_duration, right_duration)

    def _setup_listener(
        self, collection_name: CollectionName, child_uid: str, callback: Callable[[TDocumentData], None]
    ) -> None:
        """Set up real-time listener for a Firestore document.

        Generic listener setup method that works for any collection type.

        Args:
            collection_name: Name of the Firestore collection (e.g., 'sleep', 'feed', 'health', 'diaper')
            child_uid: Child unique identifier
            callback: Function to call when document changes, receives document data of the appropriate type
        """
        _LOGGER.info("Setting up real-time listener for %s/%s", collection_name, child_uid)

        client = self._get_firestore_client()
        doc_ref = client.collection(collection_name).document(child_uid)

        # Create snapshot listener
        def on_snapshot(doc_snapshot, changes, read_time):
            """Handle snapshot updates."""
            for doc in doc_snapshot:
                if doc.exists:
                    _LOGGER.debug("Real-time %s update received for child %s", collection_name, child_uid)
                    callback(doc.to_dict())

        # Start listening and store the unsubscribe function
        unsubscribe = doc_ref.on_snapshot(on_snapshot)
        listener_key = f"{collection_name}_{child_uid}"
        self._listeners[listener_key] = unsubscribe
        # Store callback for recreation after token refresh
        self._listener_callbacks[listener_key] = (collection_name, child_uid, callback)

        _LOGGER.info("Real-time %s listener active for child %s", collection_name, child_uid)

    def setup_realtime_listener(
        self, child_uid: str, callback: Callable[[SleepDocumentData], None]
    ) -> None:
        """Set up real-time listener for sleep document changes."""
        self._setup_listener("sleep", child_uid, callback)

    def setup_feed_listener(
        self, child_uid: str, callback: Callable[[FeedDocumentData], None]
    ) -> None:
        """Set up real-time listener for feed document changes."""
        self._setup_listener("feed", child_uid, callback)

    def setup_health_listener(
        self, child_uid: str, callback: Callable[[HealthDocumentData], None]
    ) -> None:
        """Set up real-time listener for health document changes."""
        self._setup_listener("health", child_uid, callback)

    def setup_diaper_listener(
        self, child_uid: str, callback: Callable[[DiaperDocumentData], None]
    ) -> None:
        """Set up real-time listener for diaper document changes."""
        self._setup_listener("diaper", child_uid, callback)

    def stop_all_listeners(self) -> None:
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
            except Exception as err:
                _LOGGER.error("Error stopping listener %s: %s", key, err)
        self._listeners.clear()
        self._listener_callbacks.clear()

    def log_diaper(self, child_uid: str, mode: DiaperMode,
                   pee_amount: DiaperAmount | None = None, poo_amount: DiaperAmount | None = None,
                   color: PooColor | None = None, consistency: PooConsistency | None = None,
                   diaper_rash: bool = False, notes: str | None = None) -> None:
        """
        Log a diaper change.

        Args:
            child_uid: Child unique identifier
            mode: One of 'pee', 'poo', 'both', 'dry'
            pee_amount: Pee amount - 'little', 'medium', 'big', or None (no quantity)
            poo_amount: Poo amount - 'little', 'medium', 'big', or None (no quantity)
            color: Poo color - 'yellow', 'brown', 'black', 'green', 'red', 'gray'
            consistency: Poo consistency - 'solid', 'loose', 'runny', 'mucousy', 'hard', 'pebbles', 'diarrhea'
            diaper_rash: Whether baby has diaper rash
            notes: Optional notes about this diaper change
        """
        _LOGGER.info("Logging diaper change for child %s: mode=%s", child_uid, mode)

        client = self._get_firestore_client()
        diaper_ref = client.collection("diaper").document(child_uid)

        current_time = time.time()

        # Create interval ID (timestamp in ms + random suffix)
        interval_timestamp_ms = int(current_time * 1000)
        interval_id = f"{interval_timestamp_ms}-{uuid.uuid4().hex[:20]}"

        # Build interval data (matching app behavior - minimal fields by default)
        interval_data: FirebaseDiaperInterval = {
            "start": current_time,
            "lastUpdated": current_time,
            "mode": mode,
            "offset": -120.0,  # Timezone offset (adjust as needed)
        }

        # Add quantity field if amounts are specified
        # App uses: 0.0 = "little", 50.0 = "medium", 100.0 = "big"
        # Other values are treated as no quantity indicator
        amount_map = {"little": 0.0, "medium": 50.0, "big": 100.0}
        quantity = {}
        if pee_amount and pee_amount in amount_map:
            quantity["pee"] = amount_map[pee_amount]
        if poo_amount and poo_amount in amount_map:
            quantity["poo"] = amount_map[poo_amount]
        if quantity:
            interval_data["quantity"] = quantity

        # Add optional fields if provided
        if color:
            interval_data["color"] = color
        if consistency:
            interval_data["consistency"] = consistency
        if diaper_rash:
            interval_data["diaperRash"] = True  # type: ignore # Not in TypedDict yet
        if notes:
            interval_data["notes"] = notes  # type: ignore # Not in TypedDict yet

        # Create interval document in subcollection
        try:
            diaper_ref.collection("intervals").document(interval_id).set(cast(dict, interval_data))
            _LOGGER.info("Created diaper interval: %s", interval_id)
        except Exception as err:
            _LOGGER.error("Failed to create diaper interval: %s", err)
            raise

        # Update prefs.lastDiaper
        try:
            last_diaper_data: LastDiaperData = {
                "start": current_time,
                "mode": mode,
                "offset": -120.0,
            }
            diaper_ref.update({
                "prefs.lastDiaper": last_diaper_data,
                "prefs.timestamp": {"seconds": current_time},
                "prefs.local_timestamp": current_time,
            })
            _LOGGER.info("Updated lastDiaper prefs")
        except Exception as err:
            _LOGGER.error("Failed to update diaper prefs: %s", err)
            raise

        _LOGGER.info("Diaper change logged successfully")

    def log_growth(self, child_uid: str, weight: float | None = None, height: float | None = None,
                   head: float | None = None, units: MeasurementUnits = "metric") -> None:
        """
        Log growth measurements (weight, height, head circumference).

        Args:
            child_uid: Child unique identifier
            weight: Weight measurement (kg for metric, lbs for imperial)
            height: Height measurement (cm for metric, inches for imperial)
            head: Head circumference (cm for metric, inches for imperial)
            units: 'metric' or 'imperial'
        """
        _LOGGER.info("Logging growth data for child %s", child_uid)

        if not any([weight, height, head]):
            raise ValueError("At least one measurement (weight, height, or head) is required")

        client = self._get_firestore_client()
        health_ref = client.collection("health").document(child_uid)

        current_time = time.time()

        # Create interval ID (timestamp in ms + random suffix)
        interval_timestamp_ms = int(current_time * 1000)
        interval_id = f"{interval_timestamp_ms}-{uuid.uuid4().hex[:20]}"

        # Build growth entry matching Huckleberry app structure
        growth_entry: FirebaseGrowthData = {
            "_id": interval_id,  # type: ignore # _id is not in TypedDict but Firestore accepts it
            "type": "health",
            "mode": "growth",
            "start": current_time,
            "lastUpdated": current_time,
            "offset": -120.0,  # Timezone offset (adjust as needed)
            "isNight": False,
            "multientry_key": None,
        }

        # Add measurements with proper unit fields (matches app structure)
        if units == "metric":
            if weight is not None:
                growth_entry["weight"] = float(weight)
                growth_entry["weightUnits"] = "kg"
            if height is not None:
                growth_entry["height"] = float(height)
                growth_entry["heightUnits"] = "cm"
            if head is not None:
                growth_entry["head"] = float(head)
                growth_entry["headUnits"] = "hcm"  # App uses "hcm" for head circumference
        else:  # imperial
            if weight is not None:
                growth_entry["weight"] = float(weight)
                growth_entry["weightUnits"] = "lbs"
            if height is not None:
                growth_entry["height"] = float(height)
                growth_entry["heightUnits"] = "in"
            if head is not None:
                growth_entry["head"] = float(head)
                growth_entry["headUnits"] = "hin"  # Head in inches

        # Create interval document in health/{child_uid}/data subcollection
        # (Health uses "data" subcollection, not "intervals" like other trackers)
        health_data_ref = health_ref.collection("data").document(interval_id)

        try:
            health_data_ref.set(cast(dict, growth_entry))
            _LOGGER.info("Created growth data entry in subcollection: %s", interval_id)
        except Exception as err:
            _LOGGER.error("Failed to create growth data entry: %s", err)
            # Continue to update prefs even if subcollection write fails

        # Update prefs.lastGrowthEntry and timestamps (matches Huckleberry app structure)
        try:
            health_ref.update({
                "prefs.lastGrowthEntry": growth_entry,
                "prefs.timestamp": {"seconds": current_time},
                "prefs.local_timestamp": current_time,
            })
            _LOGGER.info("Growth data logged successfully")
        except Exception as err:
            _LOGGER.error("Failed to log growth data: %s", err)
            raise

    def get_growth_data(self, child_uid: str) -> GrowthData:
        """
        Get the latest growth measurements for a child.

        Args:
            child_uid: Child unique identifier

        Returns:
            GrowthData containing latest growth measurements
        """
        client = self._get_firestore_client()
        health_ref = client.collection("health").document(child_uid)

        try:
            doc = health_ref.get()
            if not doc.exists:
                return {
                    "weight_units": "kg",
                    "height_units": "cm",
                    "head_units": "hcm",
                }

            health_data = doc.to_dict()
            if not health_data:
                return {
                    "weight_units": "kg",
                    "height_units": "cm",
                    "head_units": "hcm",
                }

            last_growth = health_data.get("prefs", {}).get("lastGrowthEntry", {})

            if not last_growth:
                return {
                    "weight_units": "kg",
                    "height_units": "cm",
                    "head_units": "hcm",
                }

            result: GrowthData = {
                "weight": last_growth.get("weight"),
                "height": last_growth.get("height"),
                "head": last_growth.get("head"),
                "weight_units": last_growth.get("weightUnits", "kg"),
                "height_units": last_growth.get("heightUnits", "cm"),
                "head_units": last_growth.get("headUnits", "hcm"),
                "timestamp_sec": last_growth.get("start"),
            }
            return result
        except Exception as err:
            _LOGGER.error("Failed to get growth data: %s", err)
            return {
                "weight_units": "kg",
                "height_units": "cm",
                "head_units": "hcm",
            }

    def get_calendar_events(
        self,
        child_uid: str,
        start_timestamp: int,
        end_timestamp: int,
    ) -> dict[str, list[dict]]:
        """
        Fetch all calendar events (sleep, feed, diaper, health) for a date range.

        Args:
            child_uid: Child unique identifier
            start_timestamp: Start of range (Unix timestamp in seconds)
            end_timestamp: End of range (Unix timestamp in seconds)

        Returns:
            Dictionary with event type keys and lists of event dicts
        """
        return {
            "sleep": self.get_sleep_intervals(child_uid, start_timestamp, end_timestamp),
            "feed": self.get_feed_intervals(child_uid, start_timestamp, end_timestamp),
            "diaper": self.get_diaper_intervals(child_uid, start_timestamp, end_timestamp),
            "health": self.get_health_entries(child_uid, start_timestamp, end_timestamp),
        }

    def get_sleep_intervals(
        self,
        child_uid: str,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[dict]:
        """
        Fetch sleep intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_timestamp: Start of range (Unix timestamp in seconds)
            end_timestamp: End of range (Unix timestamp in seconds)

        Returns:
            List of sleep interval dicts with 'start' and 'duration' fields
        """
        events = []
        client = self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)
        intervals_ref = sleep_ref.collection("intervals")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = intervals_ref.where(
                filter=firestore.FieldFilter("start", ">=", start_timestamp)
            ).where(
                filter=firestore.FieldFilter("start", "<", end_timestamp)
            ).order_by("start").stream()

            for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                events.append({
                    "start": data["start"],
                    "duration": data.get("duration", 0),
                })

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = intervals_ref.where(
                filter=firestore.FieldFilter("multi", "==", True)
            ).stream()

            for doc in multi_docs:
                data = doc.to_dict()
                if not data or not isinstance(data.get("data"), dict):
                    continue

                # Iterate through batched entries and filter by date
                for entry_id, entry in data["data"].items():
                    if not isinstance(entry, dict) or "start" not in entry:
                        continue

                    entry_start = entry["start"]
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    events.append({
                        "start": entry_start,
                        "duration": entry.get("duration", 0),
                    })

        except Exception as err:
            _LOGGER.error("Error fetching sleep intervals: %s", err)

        return events

    def get_feed_intervals(
        self,
        child_uid: str,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[dict]:
        """
        Fetch feeding intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_timestamp: Start of range (Unix timestamp in seconds)
            end_timestamp: End of range (Unix timestamp in seconds)

        Returns:
            List of feed interval dicts with 'start', 'leftDuration', 'rightDuration' fields
        """
        events = []
        client = self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)
        intervals_ref = feed_ref.collection("intervals")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = intervals_ref.where(
                filter=firestore.FieldFilter("start", ">=", start_timestamp)
            ).where(
                filter=firestore.FieldFilter("start", "<", end_timestamp)
            ).order_by("start").stream()

            for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                # Regular doc: durations are in minutes
                events.append({
                    "start": data["start"],
                    "leftDuration": data.get("leftDuration", 0),
                    "rightDuration": data.get("rightDuration", 0),
                    "is_multi_entry": False,
                })

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = intervals_ref.where(
                filter=firestore.FieldFilter("multi", "==", True)
            ).stream()

            for doc in multi_docs:
                data = doc.to_dict()
                if not data or not isinstance(data.get("data"), dict):
                    continue

                # Iterate through batched entries and filter by date
                for entry_id, entry in data["data"].items():
                    if not isinstance(entry, dict) or "start" not in entry:
                        continue

                    entry_start = entry["start"]
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    # Multi-entry: durations are in SECONDS
                    events.append({
                        "start": entry_start,
                        "leftDuration": entry.get("leftDuration", 0),
                        "rightDuration": entry.get("rightDuration", 0),
                        "is_multi_entry": True,
                    })

        except Exception as err:
            _LOGGER.error("Error fetching feed intervals: %s", err)

        return events

    def get_diaper_intervals(
        self,
        child_uid: str,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[dict]:
        """
        Fetch diaper intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_timestamp: Start of range (Unix timestamp in seconds)
            end_timestamp: End of range (Unix timestamp in seconds)

        Returns:
            List of diaper interval dicts with 'start', 'mode', and optional details
        """
        events = []
        client = self._get_firestore_client()
        diaper_ref = client.collection("diaper").document(child_uid)
        intervals_ref = diaper_ref.collection("intervals")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = intervals_ref.where(
                filter=firestore.FieldFilter("start", ">=", start_timestamp)
            ).where(
                filter=firestore.FieldFilter("start", "<", end_timestamp)
            ).order_by("start").stream()

            for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                event = {
                    "start": data["start"],
                    "mode": data.get("mode", "unknown"),
                }
                # Add optional fields if present
                if "pooColor" in data:
                    event["pooColor"] = data["pooColor"]
                if "pooConsistency" in data:
                    event["pooConsistency"] = data["pooConsistency"]
                if "amount" in data:
                    event["amount"] = data["amount"]
                events.append(event)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = intervals_ref.where(
                filter=firestore.FieldFilter("multi", "==", True)
            ).stream()

            for doc in multi_docs:
                data = doc.to_dict()
                if not data or not isinstance(data.get("data"), dict):
                    continue

                # Iterate through batched entries and filter by date
                for entry_id, entry in data["data"].items():
                    if not isinstance(entry, dict) or "start" not in entry:
                        continue

                    entry_start = entry["start"]
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    event = {
                        "start": entry_start,
                        "mode": entry.get("mode", "unknown"),
                    }
                    # Add optional fields if present
                    if "pooColor" in entry:
                        event["pooColor"] = entry["pooColor"]
                    if "pooConsistency" in entry:
                        event["pooConsistency"] = entry["pooConsistency"]
                    if "amount" in entry:
                        event["amount"] = entry["amount"]
                    events.append(event)

        except Exception as err:
            _LOGGER.error("Error fetching diaper intervals: %s", err)

        return events

    def get_health_entries(
        self,
        child_uid: str,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[dict]:
        """
        Fetch health/growth entries from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_timestamp: Start of range (Unix timestamp in seconds)
            end_timestamp: End of range (Unix timestamp in seconds)

        Returns:
            List of health entry dicts with 'start' and optional measurement fields
        """
        events = []
        client = self._get_firestore_client()
        health_ref = client.collection("health").document(child_uid)
        # Health uses "data" subcollection, not "intervals"
        data_ref = health_ref.collection("data")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = data_ref.where(
                filter=firestore.FieldFilter("start", ">=", start_timestamp)
            ).where(
                filter=firestore.FieldFilter("start", "<", end_timestamp)
            ).order_by("start").stream()

            for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                event = {"start": data["start"]}
                # Add optional measurement fields if present
                if "weight" in data:
                    event["weight"] = data["weight"]
                if "height" in data:
                    event["height"] = data["height"]
                if "head" in data:
                    event["head"] = data["head"]
                events.append(event)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = data_ref.where(
                filter=firestore.FieldFilter("multi", "==", True)
            ).stream()

            for doc in multi_docs:
                data = doc.to_dict()
                if not data or not isinstance(data.get("data"), dict):
                    continue

                # Iterate through batched entries and filter by date
                for entry_id, entry in data["data"].items():
                    if not isinstance(entry, dict) or "start" not in entry:
                        continue

                    entry_start = entry["start"]
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    event = {"start": entry_start}
                    # Add optional measurement fields if present
                    if "weight" in entry:
                        event["weight"] = entry["weight"]
                    if "height" in entry:
                        event["height"] = entry["height"]
                    if "head" in entry:
                        event["head"] = entry["head"]
                    events.append(event)

        except Exception as err:
            _LOGGER.error("Error fetching health entries: %s", err)

        return events
