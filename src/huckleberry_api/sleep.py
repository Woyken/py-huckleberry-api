"""Sleep tracker: live timer state and saved sleep intervals."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from google.cloud import firestore
from .client import HuckleberryBase
from .common import _LOGGER
from .firebase_types import FirebaseLastSleepData
from .firebase_types import FirebaseSleepCondition
from .firebase_types import FirebaseSleepDetails
from .firebase_types import FirebaseSleepDocumentData
from .firebase_types import FirebaseSleepIntervalData
from .firebase_types import FirebaseSleepLocations
from .firebase_types import FirebaseSleepTimerData
from .firebase_types import FirebaseTimestamp
from .firebase_types import to_firebase_dict


class SleepMixin(HuckleberryBase):
    """Sleep timer and sleep history methods."""

    async def get_sleep(self, child_uid: str) -> FirebaseSleepDocumentData | None:
        """Get the typed sleep/{child_uid} document, including live timer state."""
        _LOGGER.debug("Fetching sleep document for %s", child_uid)

        db = await self._get_firestore_client()
        sleep_doc = await db.collection("sleep").document(child_uid).get(timeout=10.0)
        if not sleep_doc.exists:
            return None

        sleep_data = sleep_doc.to_dict()
        if not sleep_data:
            return None
        return FirebaseSleepDocumentData.model_validate(sleep_data)

    async def start_sleep(self, child_uid: str, *, start_time: datetime | None = None) -> None:
        """Start sleep tracking, or adjust an already running sleep timer.

        Args:
            child_uid: Child unique identifier.
            start_time: Optional moment the child fell asleep. Defaults to now. When a
                sleep timer is already active, this moves its start instead of
                starting a new session.
        """
        _LOGGER.info("Starting sleep tracking for child %s", child_uid)

        client = await self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)

        current_time = time.time()
        requested_start = start_time.timestamp() if start_time is not None else None

        existing_doc = await sleep_ref.get(timeout=10.0)
        existing_timer = (
            FirebaseSleepDocumentData.model_validate(existing_doc.to_dict() or {}).timer
            if existing_doc.exists
            else None
        )
        if existing_timer is not None and existing_timer.active:
            if requested_start is None:
                _LOGGER.info("Sleep is already active for %s, ignoring start request", child_uid)
                return
            start_ms = requested_start * 1000
            if existing_timer.paused and existing_timer.timerEndTime is not None:
                if start_ms > float(existing_timer.timerEndTime):
                    raise ValueError("start_time must not be after the paused sleep end time")
            await sleep_ref.update(
                {
                    "timer.timerStartTime": start_ms,
                    "timer.timestamp": {"seconds": current_time},
                    "timer.local_timestamp": current_time,
                }
            )
            _LOGGER.info("Sleep start adjusted for child %s", child_uid)
            return

        # Milliseconds for timerStartTime
        current_time_ms = (requested_start if requested_start is not None else current_time) * 1000

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

    async def complete_sleep(
        self,
        child_uid: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        details: FirebaseSleepDetails | None = None,
    ) -> None:
        """Complete current sleep session and save interval.

        Args:
            child_uid: Child unique identifier.
            start_time: Optional start override for the saved interval.
            end_time: Optional end override for the saved interval. Defaults to the
                paused timer end when paused, otherwise now.
            details: Optional details override. Defaults to the timer details.
        """
        _LOGGER.info("Completing sleep for child %s", child_uid)

        now = time.time()
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
        if start_time is not None:
            timer_start_ms = start_time.timestamp() * 1000
        elif not timer_start_ms:
            # Attempt fallback: reconstruct using timestamp.seconds if available
            ts_seconds = timer.timestamp.seconds if timer.timestamp else None
            if ts_seconds:
                timer_start_ms = int(float(ts_seconds) * 1000)
                _LOGGER.warning("timerStartTime missing; falling back to timestamp.seconds for %s", child_uid)
            else:
                _LOGGER.warning("Missing timerStartTime; cannot compute duration for %s", child_uid)
                await sleep_ref.update({"timer": firestore.DELETE_FIELD})
                return

        now_ms = now * 1000

        # A caller supplied end wins over the paused timer end, because it is an
        # explicit correction. Otherwise a paused timer keeps its own end time.
        if end_time is not None:
            end_ms = end_time.timestamp() * 1000
        elif timer.paused and timer.timerEndTime is not None:
            end_ms = timer.timerEndTime
            _LOGGER.info("Sleep is paused, using timerEndTime for completion")
        else:
            end_ms = now_ms

        if float(end_ms) < float(timer_start_ms):
            raise ValueError("end_time must not precede start_time")

        duration_sec = int((end_ms - float(timer_start_ms)) / 1000)
        start_sec = int(float(timer_start_ms) / 1000)

        intervals_ref = sleep_ref.collection("intervals")
        interval_id = uuid.uuid4().hex[:16]
        sleep_interval = FirebaseSleepIntervalData(
            start=start_sec,
            duration=duration_sec,
            offset=await self._get_timezone_offset_minutes(),
            end_offset=await self._get_timezone_offset_minutes(),
            details=details if details is not None else timer.details,
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
