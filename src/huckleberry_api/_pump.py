"""Pump tracker: live timer, saved sessions, and amounts."""

from __future__ import annotations

import math
import time
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from google.cloud.firestore import DELETE_FIELD
from google.cloud.firestore_v1.async_document import AsyncDocumentReference
from pydantic import TypeAdapter
from ._client import HuckleberryBase
from ._common import _LOGGER
from .firebase_types import FirebaseLastPumpData
from .firebase_types import FirebasePumpDocumentData
from .firebase_types import FirebasePumpIntervalData
from .firebase_types import FirebasePumpTimerData
from .firebase_types import FirebaseTimestamp
from .firebase_types import PumpEntryMode
from .firebase_types import VolumeUnits
from .firebase_types import to_firebase_dict


_VOLUME_UNITS_ADAPTER = TypeAdapter(VolumeUnits)


def _resolve_pump_amounts(
    *,
    left_amount: float | int | None,
    right_amount: float | int | None,
    total_amount: float | int | None,
) -> tuple[PumpEntryMode, float, float]:
    """Validate pump amounts and convert total mode to Firebase side values."""
    if total_amount is not None:
        if left_amount is not None or right_amount is not None:
            raise ValueError("Provide either total_amount or left/right amounts, not both")
        if not math.isfinite(float(total_amount)) or float(total_amount) < 0:
            raise ValueError("total_amount must be a non-negative finite number")
        per_side_amount = float(total_amount) / 2
        return "total", per_side_amount, per_side_amount

    if left_amount is None or right_amount is None:
        raise ValueError("leftright pump entries require both left_amount and right_amount")
    if not math.isfinite(float(left_amount)) or float(left_amount) < 0:
        raise ValueError("left_amount must be a non-negative finite number")
    if not math.isfinite(float(right_amount)) or float(right_amount) < 0:
        raise ValueError("right_amount must be a non-negative finite number")
    return "leftright", float(left_amount), float(right_amount)


def _inactive_pump_timer(session_uuid: str, now_ms: int) -> FirebasePumpTimerData:
    """Build the minimal inactive timer shape observed after save and reset."""
    now = now_ms / 1000
    return FirebasePumpTimerData(
        active=False,
        timestamp=FirebaseTimestamp(seconds=now),
        local_timestamp=now,
        startTime=float(now_ms),
        uuid=session_uuid,
    )


class PumpMixin(HuckleberryBase):
    """Pump timer and pump history methods."""

    async def start_pump(self, child_uid: str, *, start_time: datetime | None = None) -> None:
        """Start a pumping timer, or adjust an already running one.

        Args:
            child_uid: Child unique identifier.
            start_time: Optional timer start. Defaults to now. When a pump timer is
                already active, this moves its start instead of starting a new
                session.
        """
        pump_ref, existing_timer = await self._get_pump_timer(child_uid)
        now_ms = int(time.time() * 1000)
        now = now_ms / 1000
        requested_start_ms = start_time.timestamp() * 1000 if start_time is not None else None

        if existing_timer and existing_timer.active:
            if requested_start_ms is None:
                _LOGGER.info("Pump timer is already active for %s", child_uid)
                return
            if existing_timer.paused and existing_timer.endTime is not None:
                if requested_start_ms > float(existing_timer.endTime):
                    raise ValueError("start_time must not be after the paused pump end time")
            await pump_ref.update(
                {
                    "timer.startTime": requested_start_ms,
                    "timer.timestamp": {"seconds": now},
                    "timer.local_timestamp": now,
                }
            )
            return

        session_uuid = existing_timer.uuid if existing_timer else uuid.uuid4().hex[:16]
        timer = FirebasePumpTimerData(
            active=True,
            paused=False,
            timestamp=FirebaseTimestamp(seconds=now),
            local_timestamp=now,
            startTime=float(requested_start_ms if requested_start_ms is not None else now_ms),
            uuid=session_uuid,
        )
        await pump_ref.set({"timer": to_firebase_dict(timer)}, merge=True)

    async def pause_pump(self, child_uid: str) -> None:
        """Pause the active pumping timer."""
        pump_ref, timer = await self._get_pump_timer(child_uid)
        if not timer or not timer.active or timer.paused:
            return

        now_ms = int(time.time() * 1000)
        now = now_ms / 1000
        await pump_ref.update(
            {
                "timer.active": True,
                "timer.paused": True,
                "timer.endTime": float(now_ms),
                "timer.timestamp": {"seconds": now},
                "timer.local_timestamp": now,
            }
        )

    async def resume_pump(self, child_uid: str) -> None:
        """Resume the paused pumping timer."""
        pump_ref, timer = await self._get_pump_timer(child_uid)
        if not timer or not timer.active or not timer.paused:
            return

        now_ms = int(time.time() * 1000)
        now = now_ms / 1000
        await pump_ref.update(
            {
                "timer.active": True,
                "timer.paused": False,
                "timer.endTime": DELETE_FIELD,
                "timer.timestamp": {"seconds": now},
                "timer.local_timestamp": now,
            }
        )

    async def cancel_pump(self, child_uid: str) -> None:
        """Cancel the active pumping timer without saving an interval."""
        pump_ref, timer = await self._get_pump_timer(child_uid)
        if not timer or not timer.active:
            return

        now_ms = int(time.time() * 1000)
        inactive_timer = _inactive_pump_timer(timer.uuid, now_ms)
        await pump_ref.update({"timer": to_firebase_dict(inactive_timer)})

    async def complete_pump(
        self,
        child_uid: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        left_amount: float | int | None = None,
        right_amount: float | int | None = None,
        total_amount: float | int | None = None,
        units: VolumeUnits | None = None,
        notes: str | None = None,
    ) -> None:
        """Complete the active pumping timer and save its amounts.

        Args:
            child_uid: Child unique identifier.
            start_time: Optional start override for the saved interval.
            end_time: Optional end override. Defaults to the paused timer end when
                paused, otherwise now.
            left_amount: Left-side amount for `leftright` entries.
            right_amount: Right-side amount for `leftright` entries.
            total_amount: Total amount for `total` entries.
            units: Volume units ("ml" or "oz").
            notes: Optional notes attached to the interval.
        """
        _resolve_pump_amounts(
            left_amount=left_amount,
            right_amount=right_amount,
            total_amount=total_amount,
        )
        if units is not None:
            units = _VOLUME_UNITS_ADAPTER.validate_python(units)

        pump_ref, timer = await self._get_pump_timer(child_uid)
        if not timer or not timer.active:
            return

        timer_start_ms = timer.startTime
        if start_time is not None:
            start_ms = start_time.timestamp() * 1000
        elif timer_start_ms is not None:
            start_ms = float(timer_start_ms)
        else:
            raise ValueError("Active pump timer is missing startTime")

        now_ms = int(time.time() * 1000)
        if end_time is not None:
            end_ms = end_time.timestamp() * 1000
        elif timer.paused and timer.endTime is not None:
            end_ms = float(timer.endTime)
        else:
            end_ms = float(now_ms)
        duration = (end_ms - start_ms) / 1000
        if duration < 0:
            raise ValueError("end_time must not precede start_time")

        await self._log_pump(
            child_uid,
            start_time=datetime.fromtimestamp(start_ms / 1000, tz=dt_timezone.utc),
            duration=duration,
            left_amount=left_amount,
            right_amount=right_amount,
            total_amount=total_amount,
            units=units or timer.units or "ml",
            notes=notes,
            update_pref_timestamps=False,
        )

        inactive_timer = _inactive_pump_timer(timer.uuid, now_ms)
        await pump_ref.update({"timer": to_firebase_dict(inactive_timer)})

    async def _get_pump_timer(self, child_uid: str) -> tuple[AsyncDocumentReference, FirebasePumpTimerData | None]:
        """Load a pump document reference and its validated timer."""
        client = await self._get_firestore_client()
        pump_ref = client.collection("pump").document(child_uid)
        pump_doc = await pump_ref.get(timeout=10.0)
        pump_data = FirebasePumpDocumentData.model_validate(pump_doc.to_dict() or {})
        return pump_ref, pump_data.timer

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
        await self._log_pump(
            child_uid,
            start_time=start_time,
            duration=duration,
            left_amount=left_amount,
            right_amount=right_amount,
            total_amount=total_amount,
            units=units,
            notes=notes,
            update_pref_timestamps=True,
        )

    async def _log_pump(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        duration: float | int | None,
        left_amount: float | int | None,
        right_amount: float | int | None,
        total_amount: float | int | None,
        units: VolumeUnits,
        notes: str | None,
        update_pref_timestamps: bool,
    ) -> None:
        """Write a validated pump interval and latest summary."""
        units = _VOLUME_UNITS_ADAPTER.validate_python(units)
        if duration is not None and (not math.isfinite(float(duration)) or float(duration) < 0):
            raise ValueError("duration must be a non-negative finite number")

        resolved_entry_mode, resolved_left_amount, resolved_right_amount = _resolve_pump_amounts(
            left_amount=left_amount,
            right_amount=right_amount,
            total_amount=total_amount,
        )

        start_timestamp = start_time.timestamp()
        current_offset = await self._get_timezone_offset_minutes()
        end_offset = current_offset if duration is not None else None

        current_time_ms = int(time.time() * 1000)
        current_time = current_time_ms / 1000
        interval_id = f"{current_time_ms}-{uuid.uuid4().hex[:20]}"
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
            if update_pref_timestamps:
                await pump_ref.update(
                    {
                        "prefs.lastPump": to_firebase_dict(last_pump),
                        "prefs.timestamp": {"seconds": current_time},
                        "prefs.local_timestamp": current_time,
                    }
                )
            else:
                await pump_ref.update({"prefs.lastPump": to_firebase_dict(last_pump)})

        _LOGGER.info(
            "Pump logged for child %s with mode %s (updated_last=%s)",
            child_uid,
            resolved_entry_mode,
            should_update_last_pump,
        )
