"""Diaper and potty tracker."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from google.api_core.exceptions import GoogleAPICallError
from typing import Literal
from ._client import HuckleberryBase
from ._common import _LOGGER
from .firebase_types import DiaperMode
from .firebase_types import FirebaseDiaperData
from .firebase_types import FirebaseDiaperDocumentData
from .firebase_types import FirebaseDiaperQuantity
from .firebase_types import FirebaseLastDiaperData
from .firebase_types import FirebaseLastPottyData
from .firebase_types import PooColor
from .firebase_types import PooConsistency
from .firebase_types import PottyResult
from .firebase_types import to_firebase_dict


class DiaperMixin(HuckleberryBase):
    """Diaper and potty event methods."""

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
