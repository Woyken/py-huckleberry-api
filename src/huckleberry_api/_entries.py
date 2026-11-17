"""Generic history entry management: list with ids, edit, delete.

Entry ids are the Firestore document id, or ``<container_doc_id>/<key>`` for rows
stored inside a batched ``multi`` container document.

After an edit or delete the tracker's ``prefs.last*`` summaries for the affected
kinds are rebuilt from the latest remaining row, because the app's "last ..." cards
read those summaries. Each summary payload mirrors the writer that produces it.
"""

from __future__ import annotations

import time
from contextlib import aclosing
from datetime import datetime
from google.cloud import firestore
from google.cloud.firestore import DELETE_FIELD
from google.cloud.firestore_v1.async_collection import AsyncCollectionReference
from google.cloud.firestore_v1.async_document import AsyncDocumentReference
from google.cloud.firestore_v1.field_path import FieldPath
from pydantic import TypeAdapter
from typing import TypeAlias
from typing import cast
from ._activity import _ACTIVITY_LAST_FIELD_BY_MODE
from ._client import HuckleberryBase
from ._common import _LOGGER
from .firebase_types import FirebaseActivityDocumentData
from .firebase_types import FirebaseActivityIntervalData
from .firebase_types import FirebaseBottleFeedIntervalData
from .firebase_types import FirebaseBreastFeedIntervalData
from .firebase_types import FirebaseDiaperData
from .firebase_types import FirebaseDiaperDocumentData
from .firebase_types import FirebaseFeedDocumentData
from .firebase_types import FirebaseFeedIntervalData
from .firebase_types import FirebaseGrowthData
from .firebase_types import FirebaseHealthDocumentData
from .firebase_types import FirebaseLastActivityData
from .firebase_types import FirebaseLastBottleData
from .firebase_types import FirebaseLastDiaperData
from .firebase_types import FirebaseLastGrowthData
from .firebase_types import FirebaseLastMedicationData
from .firebase_types import FirebaseLastNursingData
from .firebase_types import FirebaseLastPottyData
from .firebase_types import FirebaseLastPumpData
from .firebase_types import FirebaseLastSideData
from .firebase_types import FirebaseLastSleepData
from .firebase_types import FirebaseLastSolidData
from .firebase_types import FirebaseLastTemperatureData
from .firebase_types import FirebaseMedicationData
from .firebase_types import FirebasePumpDocumentData
from .firebase_types import FirebasePumpIntervalData
from .firebase_types import FirebaseSleepDocumentData
from .firebase_types import FirebaseSleepIntervalData
from .firebase_types import FirebaseSolidsFeedIntervalData
from .firebase_types import FirebaseTemperatureData
from .firebase_types import HealthDataEntry
from .firebase_types import JsonValue
from .firebase_types import _MS_EPOCH_THRESHOLD
from .firebase_types import MedicationUnits
from .firebase_types import to_firebase_dict
from .models import HistoryEntry
from .models import HistoryRow
from .models import Tracker


TrackerDocument: TypeAlias = (
    FirebaseSleepDocumentData
    | FirebaseFeedDocumentData
    | FirebaseDiaperDocumentData
    | FirebaseHealthDocumentData
    | FirebasePumpDocumentData
    | FirebaseActivityDocumentData
)

_DOCUMENT_MODELS: dict[Tracker, type[TrackerDocument]] = {
    "sleep": FirebaseSleepDocumentData,
    "feed": FirebaseFeedDocumentData,
    "diaper": FirebaseDiaperDocumentData,
    "health": FirebaseHealthDocumentData,
    "pump": FirebasePumpDocumentData,
    "activities": FirebaseActivityDocumentData,
}

# Same row models the `list_*` history readers in _history.py validate with.
_ROW_ADAPTERS: dict[Tracker, TypeAdapter[HistoryRow]] = {
    "sleep": TypeAdapter(FirebaseSleepIntervalData),
    "feed": TypeAdapter(FirebaseFeedIntervalData),
    "diaper": TypeAdapter(FirebaseDiaperData),
    "health": TypeAdapter(HealthDataEntry),
    "pump": TypeAdapter(FirebasePumpIntervalData),
    "activities": TypeAdapter(FirebaseActivityIntervalData),
}

# Summary kinds whose writer sets more than one prefs field. Every other kind is
# the single prefs field it is named after.
_KIND_FIELDS: dict[str, tuple[str, ...]] = {"lastNursing": ("lastNursing", "lastSide")}


def _validate_row(tracker: Tracker, raw: object) -> HistoryRow:
    """Validate one stored history row with the tracker's strict row model."""
    return _ROW_ADAPTERS[tracker].validate_python(raw)


def _parse_entry_id(entry_id: str) -> tuple[str, str | None]:
    """Split an entry id into the document id and, for batched rows, the row key."""
    parts = entry_id.split("/")
    if all(parts) and len(parts) in (1, 2):
        return parts[0], parts[1] if len(parts) == 2 else None
    raise ValueError(f"Invalid entry id {entry_id!r}; expected '<doc_id>' or '<container_doc_id>/<key>'")


def _container_rows(container: dict[str, object]) -> dict[str, object]:
    """Return the raw rows of a batched `multi` container document."""
    rows = container.get("data")
    if not isinstance(rows, dict):
        raise ValueError("Batched history container has no data map")
    return cast(dict[str, object], rows)


def _summary_kind(row: HistoryRow) -> str:
    """Return the prefs summary field that tracks this row's kind."""
    if isinstance(row, FirebaseSleepIntervalData):
        return "lastSleep"
    if isinstance(row, FirebaseBreastFeedIntervalData):
        return "lastNursing"
    if isinstance(row, FirebaseBottleFeedIntervalData):
        return "lastBottle"
    if isinstance(row, FirebaseSolidsFeedIntervalData):
        return "lastSolid"
    if isinstance(row, FirebaseDiaperData):
        # The shared diaper writer sends potty events (isPotty) to lastPotty.
        return "lastPotty" if row.isPotty else "lastDiaper"
    if isinstance(row, FirebaseGrowthData):
        return "lastGrowthEntry"
    if isinstance(row, FirebaseMedicationData):
        return "lastMedication"
    if isinstance(row, FirebaseTemperatureData):
        return "lastTemperature"
    if isinstance(row, FirebasePumpIntervalData):
        return "lastPump"
    return _ACTIVITY_LAST_FIELD_BY_MODE[row.mode]


def _summary_payloads(
    entry_id: str,
    row: HistoryRow,
    medication_units: MedicationUnits | None = None,
) -> dict[str, object]:
    """Build the prefs summary payloads for a row, keyed by prefs field.

    Each branch mirrors the writer named in its comment, key for key.
    `medication_units` is the current lastMedication units, which log_medicine
    carries over when the row has none.
    """
    doc_id, key = _parse_entry_id(entry_id)
    # Health summaries point at their row: `_id` is the row id and
    # `multientry_key` the batch container doc id (null for standalone rows).
    row_id = key or doc_id
    container_id = doc_id if key else None

    if isinstance(row, FirebaseSleepIntervalData):
        # SleepMixin.log_sleep / complete_sleep
        last_sleep = FirebaseLastSleepData(start=row.start, duration=row.duration, offset=row.offset)
        return {"lastSleep": to_firebase_dict(last_sleep)}
    if isinstance(row, FirebaseBreastFeedIntervalData):
        # FeedingMixin.log_nursing / complete_nursing write both fields together;
        # duration is leftDuration + rightDuration.
        last_nursing = FirebaseLastNursingData(
            mode="breast",
            start=row.start,
            duration=(row.leftDuration or 0) + (row.rightDuration or 0),
            leftDuration=row.leftDuration,
            rightDuration=row.rightDuration,
            offset=row.offset,
        )
        last_side = FirebaseLastSideData(start=row.start, lastSide=row.lastSide)
        return {"lastNursing": to_firebase_dict(last_nursing), "lastSide": to_firebase_dict(last_side)}
    if isinstance(row, FirebaseBottleFeedIntervalData):
        # FeedingMixin.log_bottle, which also sets the app's next-bottle defaults
        # prefs.bottleType/bottleAmount/bottleUnits from the same values.
        last_bottle = FirebaseLastBottleData(
            mode="bottle",
            start=row.start,
            bottleType=row.bottleType,
            bottleAmount=row.amount,
            bottleUnits=row.units,
            offset=row.offset,
        )
        payloads: dict[str, object] = {
            "lastBottle": to_firebase_dict(last_bottle),
            "bottleType": row.bottleType,
            "bottleUnits": row.units,
        }
        # log_bottle always has an amount; a stored row saved without one leaves the
        # previous default amount in place rather than writing a null.
        if row.amount is not None:
            payloads["bottleAmount"] = row.amount
        return payloads
    if isinstance(row, FirebaseSolidsFeedIntervalData):
        # SolidsMixin.log_solids omits empty notes and reactions.
        last_solid = FirebaseLastSolidData(
            mode="solids",
            start=row.start,
            foods=row.foods,
            reactions=row.reactions or None,
            notes=row.notes or None,
            offset=row.offset,
        )
        return {"lastSolid": to_firebase_dict(last_solid)}
    if isinstance(row, FirebaseDiaperData):
        # DiaperMixin._log_diaper_or_potty_event
        summary_model = FirebaseLastPottyData if row.isPotty else FirebaseLastDiaperData
        last_event = summary_model(start=row.start, mode=row.mode, offset=row.offset)
        return {_summary_kind(row): to_firebase_dict(last_event)}
    if isinstance(row, FirebaseGrowthData):
        # HealthMixin.log_growth, including its explicit null multientry_key.
        # FirebaseLastGrowthData types multientry_key as always null (no evidence of
        # batched growth summaries), so batched growth rows keep it null too.
        last_growth = FirebaseLastGrowthData(
            **row.model_dump(),
            _id=row_id,
            type="health",
            isNight=False,
            multientry_key=None,
        )
        payload = to_firebase_dict(last_growth)
        payload["multientry_key"] = None
        return {"lastGrowthEntry": payload}
    if isinstance(row, FirebaseMedicationData):
        # HealthMixin.log_medicine, including its explicit multientry_key.
        last_medication = FirebaseLastMedicationData(
            **row.model_dump(exclude={"units", "lastUpdated"}),
            lastUpdated=_summary_last_updated(row),
            _id=row_id,
            type="health",
            isNight=False,
            multientry_key=container_id,
            units=row.units if row.units is not None else medication_units,
        )
        payload = to_firebase_dict(last_medication)
        payload["multientry_key"] = container_id
        return {"lastMedication": payload}
    if isinstance(row, FirebaseTemperatureData):
        # HealthMixin.log_temperature dumps every summary key, nulls included.
        last_temperature = FirebaseLastTemperatureData(
            _id=row_id,
            type="health",
            mode="temperature",
            start=row.start,
            lastUpdated=_summary_last_updated(row),
            offset=row.offset,
            amount=row.amount,
            units=row.units,
            multientry_key=container_id,
        )
        return {"lastTemperature": last_temperature.model_dump(by_alias=True)}
    if isinstance(row, FirebasePumpIntervalData):
        # PumpMixin._log_pump
        last_pump = FirebaseLastPumpData(
            start=row.start,
            duration=row.duration,
            entryMode=row.entryMode,
            leftAmount=row.leftAmount,
            rightAmount=row.rightAmount,
            units=row.units,
            offset=row.offset,
        )
        return {"lastPump": to_firebase_dict(last_pump)}
    # ActivityMixin._log_activity
    last_activity = FirebaseLastActivityData(
        start=row.start,
        offset=row.offset,
        duration=row.duration,
        end_offset=row.end_offset,
    )
    return {_summary_kind(row): to_firebase_dict(last_activity)}


def _summary_last_updated(row: FirebaseMedicationData | FirebaseTemperatureData) -> float | int:
    """Return lastUpdated for a health summary, which requires one.

    Batched app rows can omit lastUpdated (see FirebaseTemperatureData and
    FirebaseMedicationData). Writers copy the row's lastUpdated; for such rows the
    row start is the closest recorded time.
    """
    return row.lastUpdated if row.lastUpdated is not None else row.start


def _latest_by_kind(candidates: list[tuple[str, HistoryRow]]) -> dict[str, tuple[str, HistoryRow]]:
    """Pick the row with the latest start for each summary kind."""
    latest: dict[str, tuple[str, HistoryRow]] = {}
    for entry_id, row in candidates:
        kind = _summary_kind(row)
        current = latest.get(kind)
        if current is None or float(row.start) > float(current[1].start):
            latest[kind] = (entry_id, row)
    return latest


def _summary_updates(
    kinds: set[str],
    latest: dict[str, tuple[str, HistoryRow]],
    medication_units: MedicationUnits | None,
    now: float,
) -> dict[str, object]:
    """Build the root document update that rewrites or removes each kind's summary."""
    updates: dict[str, object] = {}
    for kind in sorted(kinds):
        found = latest.get(kind)
        if found is None:
            for field in _KIND_FIELDS.get(kind, (kind,)):
                updates[f"prefs.{field}"] = DELETE_FIELD
            continue
        entry_id, row = found
        for field, payload in _summary_payloads(entry_id, row, medication_units).items():
            updates[f"prefs.{field}"] = payload
    # Writers refresh these alongside every summary write.
    updates["prefs.timestamp"] = {"seconds": now}
    updates["prefs.local_timestamp"] = now
    return updates


def _merge_changes(
    tracker: Tracker,
    raw: dict[str, object],
    current: HistoryRow,
    changes: dict[str, JsonValue],
    now: float,
) -> tuple[dict[str, object], HistoryRow]:
    """Apply Firebase-named changes to a stored row and validate the result.

    A None value removes the field. Unknown keys and changes that turn the row into
    a different row model are rejected. Fields the model does not know but the
    stored row carries are kept as stored.
    """
    model = type(current)
    allowed = {field.alias or name for name, field in model.model_fields.items()}
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise ValueError(
            f"Unknown {tracker} fields for {model.__name__}: {', '.join(unknown)}; "
            f"valid fields: {', '.join(sorted(allowed))}"
        )

    merged = dict(raw)
    for key, value in changes.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    merged["lastUpdated"] = now

    updated = _validate_row(tracker, merged)
    if type(updated) is not model:
        raise ValueError(f"Changes would turn a {model.__name__} row into {type(updated).__name__}")

    # Nested models ignore unknown keys too, so a change must survive validation
    # unchanged; otherwise it carried keys or values the schema does not define.
    dumped = updated.model_dump(by_alias=True, exclude_none=True)
    for key, value in changes.items():
        if value is not None and dumped.get(key) != value:
            raise ValueError(f"Invalid value for {key}: {value!r} is not a valid {model.__name__} value")
    for key in ("duration", "leftDuration", "rightDuration", "amount", "leftAmount", "rightAmount"):
        value = dumped.get(key)
        if key in changes and isinstance(value, int | float) and not value >= 0:
            raise ValueError(f"{key} must be non-negative")
    if "start" in changes and not 0 <= dumped["start"] < _MS_EPOCH_THRESHOLD:
        raise ValueError("start must be epoch seconds")
    return merged, updated


class EntriesMixin(HuckleberryBase):
    """List, edit, and delete saved history entries of any tracker."""

    async def get_tracker(self, child_uid: str, tracker: Tracker) -> TrackerDocument | None:
        """Get the typed `{tracker}/{child_uid}` root document (timer and prefs)."""
        client = await self._get_firestore_client()
        snapshot = await client.collection(tracker).document(child_uid).get(timeout=10.0)
        data = snapshot.to_dict() if snapshot.exists else None
        if not data:
            return None
        return _DOCUMENT_MODELS[tracker].model_validate(data)

    async def list_entries(
        self,
        child_uid: str,
        tracker: Tracker,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HistoryEntry]:
        """List history entries starting in [start_time, end_time), oldest first.

        Unlike the `list_*_intervals` readers, Firestore and validation errors raise.
        """
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        rows_ref = await self._rows_ref(child_uid, tracker)
        entries: list[HistoryEntry] = []

        regular_docs = (
            rows_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
            .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
            .order_by("start")
            .stream()
        )
        async for doc in regular_docs:
            data = doc.to_dict()
            if not data or data.get("multi"):
                continue
            entries.append(HistoryEntry(id=doc.id, tracker=tracker, data=_validate_row(tracker, data)))

        # Batched containers have no top-level start, so they are scanned whole.
        async for doc in rows_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream():
            for key, raw in _container_rows(doc.to_dict() or {}).items():
                row = _validate_row(tracker, raw)
                if start_timestamp <= float(row.start) < end_timestamp:
                    entries.append(HistoryEntry(id=f"{doc.id}/{key}", tracker=tracker, data=row))

        entries.sort(key=lambda entry: float(entry.data.start))
        return entries

    async def get_entry(self, child_uid: str, tracker: Tracker, entry_id: str) -> HistoryEntry | None:
        """Get one history entry by id, or None when it does not exist."""
        loaded = await self._load_entry(child_uid, tracker, entry_id)
        if loaded is None:
            return None
        return HistoryEntry(id=entry_id, tracker=tracker, data=loaded[3])

    async def update_entry(
        self,
        child_uid: str,
        tracker: Tracker,
        entry_id: str,
        changes: dict[str, JsonValue],
    ) -> HistoryEntry:
        """Edit a history entry and refresh the affected prefs summaries.

        Args:
            child_uid: Child unique identifier.
            tracker: Tracker collection name.
            entry_id: Entry id from `list_entries`.
            changes: Firebase field names to new values; None removes the field.

        Raises:
            ValueError: The entry does not exist, a key is not a field of the row
                model, the result fails validation, or the row would change model.
        """
        loaded = await self._load_entry(child_uid, tracker, entry_id)
        if loaded is None:
            raise ValueError(f"No {tracker} entry {entry_id!r}")
        doc_ref, key, raw, current = loaded
        merged, updated = _merge_changes(tracker, raw, current, changes, time.time())
        # Everything is read and validated before the first write, so a failure
        # leaves Firestore untouched.
        summary_updates = await self._summary_refresh(
            child_uid, tracker, {_summary_kind(current), _summary_kind(updated)}, entry_id, updated
        )

        if key is None:
            await doc_ref.set(merged)
        else:
            await doc_ref.update({FieldPath("data", key).to_api_repr(): merged})
        await (await self._tracker_ref(child_uid, tracker)).update(summary_updates)
        _LOGGER.info("Updated %s entry %s for child %s", tracker, entry_id, child_uid)
        return HistoryEntry(id=entry_id, tracker=tracker, data=updated)

    async def delete_entry(self, child_uid: str, tracker: Tracker, entry_id: str) -> None:
        """Delete a history entry and refresh the affected prefs summary.

        Raises:
            ValueError: The entry does not exist.
        """
        loaded = await self._load_entry(child_uid, tracker, entry_id)
        if loaded is None:
            raise ValueError(f"No {tracker} entry {entry_id!r}")
        doc_ref, key, _raw, current = loaded
        # Read and validate before the first write, as in update_entry.
        summary_updates = await self._summary_refresh(child_uid, tracker, {_summary_kind(current)}, entry_id, None)

        if key is None:
            await doc_ref.delete()
        else:
            await doc_ref.update({FieldPath("data", key).to_api_repr(): DELETE_FIELD})
        await (await self._tracker_ref(child_uid, tracker)).update(summary_updates)
        _LOGGER.info("Deleted %s entry %s for child %s", tracker, entry_id, child_uid)

    async def _tracker_ref(self, child_uid: str, tracker: Tracker) -> AsyncDocumentReference:
        """Return the `{tracker}/{child_uid}` root document reference."""
        client = await self._get_firestore_client()
        return client.collection(tracker).document(child_uid)

    async def _rows_ref(self, child_uid: str, tracker: Tracker) -> AsyncCollectionReference:
        """Return the history subcollection: `data` for health, `intervals` otherwise."""
        tracker_ref = await self._tracker_ref(child_uid, tracker)
        return tracker_ref.collection("data" if tracker == "health" else "intervals")

    async def _load_entry(
        self, child_uid: str, tracker: Tracker, entry_id: str
    ) -> tuple[AsyncDocumentReference, str | None, dict[str, object], HistoryRow] | None:
        """Load an entry's document ref, batch key, raw row, and validated row."""
        doc_id, key = _parse_entry_id(entry_id)
        doc_ref = (await self._rows_ref(child_uid, tracker)).document(doc_id)
        snapshot = await doc_ref.get(timeout=10.0)
        data = snapshot.to_dict() if snapshot.exists else None
        if not data:
            return None

        # A container is not an entry itself, and a standalone row has no keyed rows.
        if bool(data.get("multi")) != (key is not None):
            return None
        raw = data if key is None else _container_rows(data).get(key)
        if not isinstance(raw, dict):
            return None
        return doc_ref, key, cast(dict[str, object], raw), _validate_row(tracker, raw)

    async def _summary_refresh(
        self,
        child_uid: str,
        tracker: Tracker,
        kinds: set[str],
        entry_id: str,
        replacement: HistoryRow | None,
    ) -> dict[str, object]:
        """Build the root update that rebuilds each kind's prefs summary.

        Computed before the entry is written: the stored `entry_id` row is skipped
        and `replacement` (the edited row, or None for a delete) stands in for it.
        """
        rows_ref = await self._rows_ref(child_uid, tracker)
        candidates: list[tuple[str, HistoryRow]] = [] if replacement is None else [(entry_id, replacement)]

        # Batched rows carry no indexed start, so every container is read.
        async for doc in rows_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream():
            for key, raw in _container_rows(doc.to_dict() or {}).items():
                row = _validate_row(tracker, raw)
                if _summary_kind(row) in kinds and f"{doc.id}/{key}" != entry_id:
                    candidates.append((f"{doc.id}/{key}", row))

        # Standalone rows newest first on the single-field `start` index (no
        # composite index); stop once every kind has its latest row.
        # ponytail: a kind with no standalone rows left scans the whole collection;
        # cap it if histories grow large enough for that to matter.
        pending = set(kinds)
        newest_first = rows_ref.order_by("start", direction=firestore.Query.DESCENDING).stream()
        async with aclosing(newest_first) as docs:
            async for doc in docs:
                data = doc.to_dict()
                if not data or data.get("multi") or doc.id == entry_id:
                    continue
                row = _validate_row(tracker, data)
                kind = _summary_kind(row)
                if kind in pending:
                    pending.discard(kind)
                    candidates.append((doc.id, row))
                    if not pending:
                        break

        medication_units: MedicationUnits | None = None
        if "lastMedication" in kinds:
            health = await self.get_tracker(child_uid, tracker)
            if isinstance(health, FirebaseHealthDocumentData) and health.prefs and health.prefs.lastMedication:
                medication_units = health.prefs.lastMedication.units

        return _summary_updates(kinds, _latest_by_kind(candidates), medication_units, time.time())
