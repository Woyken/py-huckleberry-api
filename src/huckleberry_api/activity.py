"""Activity tracker: timers and logged activities."""

from __future__ import annotations

import math
import time
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from google.cloud.firestore_v1.async_document import AsyncDocumentReference
from pydantic import TypeAdapter
from .client import HuckleberryBase
from .common import _LOGGER
from .firebase_types import ActivityMode
from .firebase_types import FirebaseActivityDocumentData
from .firebase_types import FirebaseActivityIntervalData
from .firebase_types import FirebaseActivityTimerEntryData
from .firebase_types import FirebaseLastActivityData
from .firebase_types import FirebaseTimestamp
from .firebase_types import to_firebase_dict


_ACTIVITY_MODE_ADAPTER = TypeAdapter(ActivityMode)

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


def _activity_duration(start_time_ms: float, end_time_ms: int) -> int:
    """Round elapsed activity time to whole seconds like the app."""
    elapsed_ms = end_time_ms - start_time_ms
    if elapsed_ms < 0:
        raise ValueError("Activity timer end time must not precede start time")
    return int((elapsed_ms + 500) // 1000)


class ActivityMixin(HuckleberryBase):
    """Activity timer and activity history methods."""

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
        await self._log_activity(
            child_uid,
            mode=mode,
            start_time=start_time,
            duration=duration,
            notes=notes,
            update_timer=True,
        )

    async def _log_activity(
        self,
        child_uid: str,
        *,
        mode: ActivityMode,
        start_time: datetime,
        duration: float | int | None,
        notes: str | None,
        update_timer: bool,
    ) -> None:
        """Write an activity interval and latest summary."""
        mode = _ACTIVITY_MODE_ADAPTER.validate_python(mode)
        if duration is not None and (not math.isfinite(float(duration)) or float(duration) < 0):
            raise ValueError("duration must be a non-negative finite number")

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

        if update_timer:
            session_uuid = self._activity_uuid(activities_model)
            inactive_timer = FirebaseActivityTimerEntryData(
                active=False,
                paused=False,
                timestamp=FirebaseTimestamp(seconds=current_time),
                local_timestamp=current_time,
                startTime=float(int(current_time * 1000)),
                duration=float(duration) if duration is not None else 0,
                notes="",
                uuid=session_uuid,
            )
            await activities_ref.set(
                {"timer": {mode: to_firebase_dict(inactive_timer)}},
                merge=True,
            )

        _LOGGER.info(
            "Activity logged for child %s with mode %s (updated_last=%s)",
            child_uid,
            mode,
            should_update_last_activity,
        )

    async def start_activity(self, child_uid: str, mode: ActivityMode, *, start_time: datetime | None = None) -> None:
        """Start an activity timer, or adjust an already running one.

        Args:
            child_uid: Child unique identifier.
            mode: Activity mode.
            start_time: Optional timer start. Defaults to now. When the same mode is
                already active, this moves its start instead of starting a new
                session.
        """
        mode = _ACTIVITY_MODE_ADAPTER.validate_python(mode)
        activities_ref, activities = await self._get_activity_document(child_uid)
        active_mode = self._active_activity_mode(activities)
        now_ms = int(time.time() * 1000)
        now = now_ms / 1000
        requested_start_ms = start_time.timestamp() * 1000 if start_time is not None else None

        if active_mode == mode:
            if requested_start_ms is None:
                return
            timer = self._activity_timer(activities, mode)
            assert timer is not None
            if timer.paused and timer.endTime is not None:
                if requested_start_ms > float(timer.endTime):
                    raise ValueError("start_time must not be after the paused activity end time")
            update_data: dict[str, object] = {
                f"timer.{mode}.startTime": float(requested_start_ms),
                f"timer.{mode}.timestamp": {"seconds": now},
                f"timer.{mode}.local_timestamp": now,
            }
            if timer.paused and timer.endTime is not None:
                update_data[f"timer.{mode}.duration"] = _activity_duration(
                    float(requested_start_ms), int(float(timer.endTime))
                )
            await activities_ref.update(update_data)
            return
        if active_mode is not None:
            raise ValueError(f"{active_mode} is already active; use switch_activity()")

        timer = FirebaseActivityTimerEntryData(
            active=True,
            paused=False,
            timestamp=FirebaseTimestamp(seconds=now),
            local_timestamp=now,
            startTime=float(requested_start_ms if requested_start_ms is not None else now_ms),
            duration=0,
            notes="",
            uuid=self._activity_uuid(activities),
        )
        await activities_ref.set({"timer": {mode: to_firebase_dict(timer)}}, merge=True)

    async def pause_activity(self, child_uid: str, mode: ActivityMode) -> None:
        """Pause an active activity timer."""
        mode = _ACTIVITY_MODE_ADAPTER.validate_python(mode)
        activities_ref, activities = await self._get_activity_document(child_uid)
        timer = self._activity_timer(activities, mode)
        if not timer or not timer.active or timer.paused:
            return
        if timer.startTime is None:
            raise ValueError("Active activity timer is missing startTime")

        now_ms = int(time.time() * 1000)
        now = now_ms / 1000
        duration = _activity_duration(float(timer.startTime), now_ms)
        await activities_ref.set(
            {
                "timer": {
                    mode: {
                        "active": True,
                        "paused": True,
                        "timestamp": {"seconds": now},
                        "local_timestamp": now,
                        "duration": duration,
                        "endTime": float(timer.startTime) + duration * 1000,
                    }
                }
            },
            merge=True,
        )

    async def resume_activity(self, child_uid: str, mode: ActivityMode) -> None:
        """Resume a paused activity timer, including paused wall-clock time."""
        mode = _ACTIVITY_MODE_ADAPTER.validate_python(mode)
        activities_ref, activities = await self._get_activity_document(child_uid)
        timer = self._activity_timer(activities, mode)
        if not timer or not timer.active or not timer.paused:
            return
        if timer.startTime is None:
            raise ValueError("Active activity timer is missing startTime")

        now_ms = int(time.time() * 1000)
        now = now_ms / 1000
        duration = _activity_duration(float(timer.startTime), now_ms)
        await activities_ref.set(
            {
                "timer": {
                    mode: {
                        "active": True,
                        "paused": False,
                        "timestamp": {"seconds": now},
                        "local_timestamp": now,
                        "duration": duration,
                    }
                }
            },
            merge=True,
        )

    async def cancel_activity(self, child_uid: str, mode: ActivityMode) -> None:
        """Cancel an active activity timer without saving history."""
        mode = _ACTIVITY_MODE_ADAPTER.validate_python(mode)
        activities_ref, activities = await self._get_activity_document(child_uid)
        timer = self._activity_timer(activities, mode)
        if not timer or not timer.active:
            return
        if timer.startTime is None:
            raise ValueError("Active activity timer is missing startTime")

        now_ms = int(time.time() * 1000)
        duration = (
            int(float(timer.duration or 0)) if timer.paused else _activity_duration(float(timer.startTime), now_ms)
        )
        await self._reset_activity_timer(
            activities_ref,
            mode,
            timer,
            now_ms=now_ms,
            duration=duration,
        )

    async def complete_activity(
        self,
        child_uid: str,
        mode: ActivityMode,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        duration: float | int | None = None,
        notes: str | None = None,
    ) -> None:
        """Complete an activity timer, optionally applying edits from the save form.

        Args:
            child_uid: Child unique identifier.
            mode: Activity mode.
            start_time: Optional start override for the saved interval.
            end_time: Optional end override. Derived into a whole-second duration.
            duration: Optional duration override in seconds. Cannot be combined with
                ``end_time``.
            notes: Optional notes attached to the interval.
        """
        mode = _ACTIVITY_MODE_ADAPTER.validate_python(mode)
        if end_time is not None and duration is not None:
            raise ValueError("Provide either end_time or duration, not both")
        if duration is not None and (not math.isfinite(float(duration)) or float(duration) < 0):
            raise ValueError("duration must be a non-negative finite number")

        activities_ref, activities = await self._get_activity_document(child_uid)
        timer = self._activity_timer(activities, mode)
        if not timer or not timer.active:
            return

        timer_start_ms = timer.startTime
        if start_time is not None:
            resolved_start = start_time
        elif timer_start_ms is not None:
            resolved_start = datetime.fromtimestamp(float(timer_start_ms) / 1000, tz=dt_timezone.utc)
        else:
            raise ValueError("Active activity timer is missing startTime")

        now_ms = int(time.time() * 1000)
        resolved_start_ms = resolved_start.timestamp() * 1000
        if end_time is not None:
            resolved_duration = float(_activity_duration(resolved_start_ms, int(end_time.timestamp() * 1000)))
        elif duration is not None:
            resolved_duration = float(duration)
        elif timer.paused:
            resolved_duration = float(timer.duration or 0)
        else:
            resolved_duration = float(_activity_duration(resolved_start_ms, now_ms))
        await self._log_activity(
            child_uid,
            mode=mode,
            start_time=resolved_start,
            duration=resolved_duration,
            notes=notes,
            update_timer=False,
        )
        await self._reset_activity_timer(
            activities_ref,
            mode,
            timer,
            now_ms=now_ms,
            duration=resolved_duration,
            fallback_start_time_ms=resolved_start_ms,
        )

    async def switch_activity(self, child_uid: str, mode: ActivityMode) -> None:
        """Associate the current activity timer with another activity mode."""
        mode = _ACTIVITY_MODE_ADAPTER.validate_python(mode)
        activities_ref, activities = await self._get_activity_document(child_uid)
        source_mode = self._active_activity_mode(activities)
        if source_mode is None or source_mode == mode:
            return

        source = self._activity_timer(activities, source_mode)
        assert source is not None
        if source.startTime is None:
            raise ValueError("Active activity timer is missing startTime")

        now_ms = int(time.time() * 1000)
        now = now_ms / 1000
        duration = (
            float(source.duration or 0) if source.paused else float(_activity_duration(float(source.startTime), now_ms))
        )
        target: dict[str, object] = {
            "active": True,
            "paused": bool(source.paused),
            "timestamp": {"seconds": now},
            "local_timestamp": now,
            "startTime": source.startTime,
            "duration": duration,
            "notes": source.notes or "",
            "uuid": source.uuid,
        }
        if source.paused and source.endTime is not None:
            target["endTime"] = source.endTime

        await activities_ref.set(
            {
                "timer": {
                    source_mode: {
                        "active": False,
                        "paused": False,
                        "timestamp": {"seconds": now},
                        "local_timestamp": now,
                        "startTime": float(now_ms),
                    },
                    mode: target,
                }
            },
            merge=True,
        )

    async def _get_activity_document(
        self, child_uid: str
    ) -> tuple[AsyncDocumentReference, FirebaseActivityDocumentData]:
        """Load an activities document reference and validated data."""
        client = await self._get_firestore_client()
        activities_ref = client.collection("activities").document(child_uid)
        activities_doc = await activities_ref.get(timeout=10.0)
        activities = FirebaseActivityDocumentData.model_validate(activities_doc.to_dict() or {})
        return activities_ref, activities

    @staticmethod
    def _activity_timer(
        activities: FirebaseActivityDocumentData, mode: ActivityMode
    ) -> FirebaseActivityTimerEntryData | None:
        """Return one mode's timer entry."""
        return getattr(activities.timer, mode) if activities.timer else None

    @classmethod
    def _active_activity_mode(cls, activities: FirebaseActivityDocumentData) -> ActivityMode | None:
        """Return the active activity mode, if any."""
        for mode in _ACTIVITY_LAST_FIELD_BY_MODE:
            timer = cls._activity_timer(activities, mode)
            if timer and timer.active:
                return mode
        return None

    @classmethod
    def _activity_uuid(cls, activities: FirebaseActivityDocumentData) -> str:
        """Reuse the shared activity timer UUID used by the app."""
        for mode in _ACTIVITY_LAST_FIELD_BY_MODE:
            timer = cls._activity_timer(activities, mode)
            if timer:
                return timer.uuid
        return uuid.uuid4().hex[:16]

    @staticmethod
    async def _reset_activity_timer(
        activities_ref: AsyncDocumentReference,
        mode: ActivityMode,
        timer: FirebaseActivityTimerEntryData,
        *,
        now_ms: int,
        duration: float | int,
        fallback_start_time_ms: float | None = None,
    ) -> None:
        """Write the inactive timer shape shared by save and reset."""
        # Keep the original timer start for the stored inactive timer when present,
        # matching the app's frozen timer timeline. The saved interval uses the
        # resolved start; the fallback only covers malformed timers without one.
        resolved_start_ms = timer.startTime if timer.startTime is not None else fallback_start_time_ms
        assert resolved_start_ms is not None
        now = now_ms / 1000
        await activities_ref.set(
            {
                "timer": {
                    mode: {
                        "active": False,
                        "paused": False,
                        "timestamp": {"seconds": now},
                        "local_timestamp": now,
                        "startTime": float(now_ms),
                        "endTime": float(resolved_start_ms) + float(duration) * 1000,
                        "duration": duration,
                        "notes": timer.notes or "",
                        "uuid": timer.uuid,
                    }
                }
            },
            merge=True,
        )
