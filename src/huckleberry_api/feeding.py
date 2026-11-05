"""Feeding tracker: nursing timers and bottle entries."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from google.api_core.exceptions import GoogleAPICallError
from google.cloud.firestore import DELETE_FIELD
from .client import HuckleberryBase
from .common import _LOGGER
from .firebase_types import BottleType
from .firebase_types import FeedSide
from .firebase_types import FirebaseBottleFeedIntervalData
from .firebase_types import FirebaseBreastFeedIntervalData
from .firebase_types import FirebaseFeedDocumentData
from .firebase_types import FirebaseFeedTimerData
from .firebase_types import FirebaseLastBottleData
from .firebase_types import FirebaseLastNursingData
from .firebase_types import FirebaseLastSideData
from .firebase_types import FirebaseTimestamp
from .firebase_types import VolumeUnits
from .firebase_types import to_firebase_dict


class FeedingMixin(HuckleberryBase):
    """Nursing timer, nursing history, and bottle feeding methods."""

    async def get_nursing(self, child_uid: str) -> FirebaseFeedDocumentData | None:
        """Get the typed feed/{child_uid} document, including live nursing timer state."""
        _LOGGER.debug("Fetching feed document for %s", child_uid)

        db = await self._get_firestore_client()
        feed_doc = await db.collection("feed").document(child_uid).get(timeout=10.0)
        if not feed_doc.exists:
            return None

        feed_data = feed_doc.to_dict()
        if not feed_data:
            return None
        return FirebaseFeedDocumentData.model_validate(feed_data)

    async def start_nursing(
        self, child_uid: str, side: FeedSide = "left", *, start_time: datetime | None = None
    ) -> None:
        """Start nursing tracking, or adjust an already running session.

        Args:
            child_uid: Child unique identifier.
            side: Side to start on.
            start_time: Optional session start. Defaults to now. When nursing is
                already active, this shifts the session start instead of starting a
                new session.
        """
        _LOGGER.info("Starting nursing for child %s on %s side", child_uid, side)

        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        current_time = time.time()
        requested_start = start_time.timestamp() if start_time is not None else None

        doc = await feed_ref.get(timeout=10.0)
        existing_timer = FirebaseFeedDocumentData.model_validate(doc.to_dict() or {}).timer if doc.exists else None
        if existing_timer is not None and existing_timer.active:
            if requested_start is None:
                _LOGGER.info("Nursing is already active for %s, ignoring start request", child_uid)
                return
            previous_start = float(existing_timer.feedStartTime or existing_timer.timerStartTime or requested_start)
            shift = requested_start - previous_start
            segment_start = float(existing_timer.timerStartTime or previous_start) + shift
            if not existing_timer.paused and segment_start > current_time:
                # Moving the session start later must not push the running segment
                # past now. Keep its start so the elapsed time stays valid.
                segment_start = float(existing_timer.timerStartTime or previous_start)
                _LOGGER.warning(
                    "start_time would move the current nursing segment past now; keeping segment start for %s",
                    child_uid,
                )
            await feed_ref.update(
                {
                    "timer.feedStartTime": requested_start,
                    "timer.timerStartTime": segment_start,
                    "timer.timestamp": {"seconds": current_time},
                    "timer.local_timestamp": current_time,
                }
            )
            _LOGGER.info("Nursing start adjusted for child %s", child_uid)
            return

        session_start = requested_start if requested_start is not None else current_time

        session_uuid = uuid.uuid4().hex[:16]

        feed_data = FirebaseFeedDocumentData(
            timer=FirebaseFeedTimerData(
                active=True,
                paused=False,
                timestamp=FirebaseTimestamp(seconds=current_time),
                local_timestamp=current_time,
                feedStartTime=session_start,
                timerStartTime=session_start,
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

    async def complete_nursing(
        self,
        child_uid: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        """Complete current nursing and save to history.

        Args:
            child_uid: Child unique identifier.
            start_time: Optional session start override for the saved interval.
            end_time: Optional end of the current nursing segment. Defaults to now.
        """
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
        end_timestamp = end_time.timestamp() if end_time is not None else now_time
        # timerStartTime is in seconds for feeding
        timer_start_sec = float(timer_start)

        left_duration = timer.leftDuration or 0.0
        right_duration = timer.rightDuration or 0.0

        # Add elapsed time on current side if not paused
        if not timer.paused:
            elapsed = end_timestamp - timer_start_sec
            if elapsed < 0:
                raise ValueError("end_time must not precede the current nursing segment start")
            current_side = timer.activeSide or timer.lastSide or "left"

            if current_side == "left":
                left_duration += elapsed
            else:
                right_duration += elapsed

        # Calculate total duration from accumulated durations
        total_duration = left_duration + right_duration

        feed_start_time = (
            start_time.timestamp() if start_time is not None else float(timer.feedStartTime or timer_start_sec)
        )
        if feed_start_time > end_timestamp:
            raise ValueError("start_time must not be after end_time")

        # A corrected start can make the accumulated side durations exceed the
        # corrected session span (for example, when a running start is moved
        # forward and the segment start is kept). Trim the active side first so
        # the saved interval stays internally consistent.
        max_duration = max(0.0, end_timestamp - feed_start_time)
        if total_duration > max_duration:
            original_total = total_duration
            excess = total_duration - max_duration
            trim_side = timer.activeSide or timer.lastSide or "left"
            if trim_side == "left":
                trim = min(excess, left_duration)
                left_duration -= trim
                excess -= trim
                if excess > 0:
                    right_duration = max(0.0, right_duration - excess)
            else:
                trim = min(excess, right_duration)
                right_duration -= trim
                excess -= trim
                if excess > 0:
                    left_duration = max(0.0, left_duration - excess)
            total_duration = left_duration + right_duration
            _LOGGER.warning(
                "Trimmed %.1fs from nursing durations to fit the corrected session for %s",
                original_total - total_duration,
                child_uid,
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

        await feed_intervals_ref.set(to_firebase_dict(breast_interval))
        _LOGGER.info("Created nursing interval entry: %s", interval_id)

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
        notes: str | None = None,
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
            notes=notes,
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
        notes: str | None = None,
    ) -> None:
        """Log bottle feeding as instant event.

        Args:
            child_uid: Child unique identifier
            start_time: Event time
            bottle_type: Type of bottle contents ("Breast Milk", "Formula", "Cow Milk", etc.)
            amount: Amount fed in specified units
            units: Volume units ("ml" or "oz")
            notes: Optional notes attached to the interval.
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
            notes=notes,
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
