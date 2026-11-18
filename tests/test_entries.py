"""Tests for generic history entry management (list with ids, edit, delete)."""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import cast
from unittest.mock import AsyncMock
from unittest.mock import patch

import aiohttp
import pytest
from google.cloud.firestore import DELETE_FIELD
from google.cloud.firestore_v1.field_path import FieldPath
from pydantic import ValidationError

from huckleberry_api import HuckleberryAPI
from huckleberry_api._entries import (
    _latest_by_kind,
    _merge_changes,
    _parse_entry_id,
    _summary_payloads,
    _summary_updates,
    _validate_row,
)
from huckleberry_api.firebase_types import JsonValue
from huckleberry_api.models import HistoryRow, Tracker

NOW = 1_800_000_000.0


# ---------------------------------------------------------------------------
# Pure unit tests (no network)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry_id", "expected"),
    [("doc1", ("doc1", None)), ("container/key-1", ("container", "key-1"))],
)
def test_parse_entry_id(entry_id: str, expected: tuple[str, str | None]) -> None:
    assert _parse_entry_id(entry_id) == expected


@pytest.mark.parametrize("entry_id", ["", "/", "a/", "/b", "a/b/c", "a//b"])
def test_parse_entry_id_rejects_malformed(entry_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid entry id"):
        _parse_entry_id(entry_id)


BOTTLE_RAW: dict[str, object] = {
    "mode": "bottle",
    "start": 100.0,
    "lastUpdated": 100.0,
    "bottleType": "Formula",
    "amount": 120.0,
    "units": "ml",
    "offset": -60.0,
    "end_offset": -60.0,
    "notes": "old",
    "appOnlyField": "kept",
}


def _merge(tracker: Tracker, raw: dict[str, object], changes: dict[str, JsonValue]):
    return _merge_changes(tracker, raw, _validate_row(tracker, raw), changes, NOW)


def test_merge_changes_applies_values_and_removals() -> None:
    merged, row = _merge("feed", BOTTLE_RAW, {"amount": 90, "notes": None})
    assert merged["amount"] == 90
    assert "notes" not in merged
    assert merged["lastUpdated"] == NOW
    # Fields the strict model ignores stay as stored.
    assert merged["appOnlyField"] == "kept"
    assert row.model_dump(by_alias=True)["amount"] == 90
    assert BOTTLE_RAW["notes"] == "old", "stored row must not be mutated"


def test_merge_changes_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="Unknown feed fields.*leftDuration"):
        _merge("feed", BOTTLE_RAW, {"leftDuration": 10})


def test_merge_changes_rejects_model_switch() -> None:
    # Keys of another row model are rejected outright...
    with pytest.raises(ValueError, match="Unknown feed fields"):
        _merge("feed", BOTTLE_RAW, {"mode": "breast", "lastSide": "left"})
    # ...and a stored extra that completes another model cannot smuggle a switch through.
    raw_with_extra = {**BOTTLE_RAW, "lastSide": "left"}
    with pytest.raises(ValueError, match="FirebaseBottleFeedIntervalData row into FirebaseBreastFeedIntervalData"):
        _merge("feed", raw_with_extra, {"mode": "breast"})


def test_merge_changes_rejects_invalid_value_and_required_removal() -> None:
    with pytest.raises(ValidationError):
        _merge("feed", BOTTLE_RAW, {"units": "gallons"})
    with pytest.raises(ValidationError):
        _merge("feed", BOTTLE_RAW, {"bottleType": None})


def test_merge_changes_allows_mode_within_same_model() -> None:
    raw: dict[str, object] = {"mode": "pee", "start": 1.0, "offset": 0.0}
    _merged, row = _merge("diaper", raw, {"mode": "poo", "color": "yellow"})
    assert row.model_dump()["mode"] == "poo"


def test_merge_changes_accepts_alias_field() -> None:
    raw: dict[str, object] = {"start": 1.0, "duration": 5.0, "offset": 0.0}
    merged, _row = _merge("sleep", raw, {"_id": "abc"})
    assert merged["_id"] == "abc"


def test_merge_changes_rejects_invented_nested_keys() -> None:
    sleep: dict[str, object] = {"start": 1.0, "duration": 5.0, "offset": 0.0}
    with pytest.raises(ValueError, match="Invalid value for details"):
        _merge("sleep", sleep, {"details": {"inventedKey": 1, "sleepLocations": {"hammock": True}}})
    diaper: dict[str, object] = {"mode": "pee", "start": 1.0, "offset": 0.0}
    with pytest.raises(ValueError, match="Invalid value for quantity"):
        _merge("diaper", diaper, {"quantity": {"volume_ml": 3}})
    # Known nested keys and int-for-float values pass.
    _merge("sleep", sleep, {"details": {"notes": "x", "sleepLocations": {"car": True}}})
    _merge("diaper", diaper, {"quantity": {"pee": 50}})


@pytest.mark.parametrize(
    ("tracker", "raw", "changes", "message"),
    [
        ("sleep", {"start": 1.0, "duration": 5.0, "offset": 0.0}, {"duration": -1}, "duration must be"),
        (
            "feed",
            {"mode": "breast", "start": 1.0, "lastSide": "left", "offset": 0.0},
            {"rightDuration": -5.0},
            "rightDuration must be",
        ),
        (
            "feed",
            {"mode": "bottle", "start": 1.0, "bottleType": "Formula", "amount": 60.0, "units": "ml", "offset": 0.0},
            {"amount": -5.0},
            "amount must be",
        ),
        ("sleep", {"start": 1.0, "duration": 5.0, "offset": 0.0}, {"start": 1_800_000_000_000}, "epoch seconds"),
        ("sleep", {"start": 1.0, "duration": 5.0, "offset": 0.0}, {"start": -1}, "epoch seconds"),
    ],
)
def test_merge_changes_rejects_implausible_numbers(
    tracker: Tracker, raw: dict[str, object], changes: dict[str, JsonValue], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _merge(tracker, raw, changes)


def test_unknown_field_error_lists_valid_fields() -> None:
    with pytest.raises(ValueError, match="valid fields: .*amount.*bottleType"):
        _merge("feed", BOTTLE_RAW, {"volume": 1})


# Each case: tracker, raw row, entry id, expected payloads (matching the writer named).
SUMMARY_CASES: list[tuple[str, Tracker, dict[str, object], str, dict[str, object]]] = [
    (
        "sleep",
        "sleep",
        {"start": 10, "duration": 60, "offset": -60.0, "end_offset": -60.0, "lastUpdated": 70.0},
        "s1",
        {"lastSleep": {"start": 10, "duration": 60, "offset": -60.0}},
    ),
    (
        "nursing",
        "feed",
        {
            "mode": "breast",
            "start": 10.0,
            "lastSide": "right",
            "leftDuration": 30.0,
            "rightDuration": 45.0,
            "offset": -60.0,
            "end_offset": -60.0,
            "notes": "n",
        },
        "f1",
        {
            "lastNursing": {
                "mode": "breast",
                "start": 10.0,
                "duration": 75.0,
                "leftDuration": 30.0,
                "rightDuration": 45.0,
                "offset": -60.0,
            },
            "lastSide": {"start": 10.0, "lastSide": "right"},
        },
    ),
    (
        "bottle",
        "feed",
        BOTTLE_RAW,
        "f2",
        {
            "lastBottle": {
                "mode": "bottle",
                "start": 100.0,
                "bottleType": "Formula",
                "bottleAmount": 120.0,
                "bottleUnits": "ml",
                "offset": -60.0,
            },
            # log_bottle also refreshes the next-bottle defaults.
            "bottleType": "Formula",
            "bottleAmount": 120.0,
            "bottleUnits": "ml",
        },
    ),
    (
        "solids",
        "feed",
        {
            "mode": "solids",
            "start": 10.0,
            "offset": 0.0,
            "notes": "",
            "foods": {"apple": {"id": "apple", "created_name": "Apple", "source": "curated", "amount": "some"}},
        },
        "f3",
        {
            "lastSolid": {
                "mode": "solids",
                "start": 10.0,
                "offset": 0.0,
                "foods": {"apple": {"id": "apple", "created_name": "Apple", "source": "curated", "amount": "some"}},
            }
        },
    ),
    (
        "diaper",
        "diaper",
        {"mode": "both", "start": 10.0, "offset": 0.0, "color": "yellow", "notes": "x"},
        "d1",
        {"lastDiaper": {"start": 10.0, "mode": "both", "offset": 0.0}},
    ),
    (
        "potty",
        "diaper",
        {"mode": "pee", "start": 10.0, "offset": 0.0, "isPotty": True, "howItHappened": "wentPotty"},
        "d2",
        {"lastPotty": {"mode": "pee", "start": 10.0, "offset": 0.0}},
    ),
    (
        "growth",
        "health",
        {"mode": "growth", "start": 10.0, "lastUpdated": 11.0, "offset": 0.0, "weight": 5.0, "weightUnits": "kg"},
        "h1",
        {
            "lastGrowthEntry": {
                "mode": "growth",
                "start": 10.0,
                "lastUpdated": 11.0,
                "offset": 0.0,
                "weight": 5.0,
                "weightUnits": "kg",
                "_id": "h1",
                "type": "health",
                "isNight": False,
                "multientry_key": None,
            }
        },
    ),
    (
        "medication",
        "health",
        {
            "mode": "medication",
            "start": 10.0,
            "lastUpdated": 11.0,
            "offset": 0.0,
            "medication_id": "m",
            "medication_name": "Med",
            "amount": 2.5,
            "units": "ml",
            "notes": "",
        },
        "h2",
        {
            "lastMedication": {
                "mode": "medication",
                "start": 10.0,
                "lastUpdated": 11.0,
                "offset": 0.0,
                "medication_id": "m",
                "medication_name": "Med",
                "amount": 2.5,
                "units": "ml",
                "notes": "",
                "_id": "h2",
                "type": "health",
                "isNight": False,
                "multientry_key": None,
            }
        },
    ),
    (
        "batched medication without units or lastUpdated",
        "health",
        {
            "mode": "medication",
            "start": 10.0,
            "offset": 0.0,
            "medication_id": "m",
            "medication_name": "Med",
            "amount": 0.0,
            "notes": "",
        },
        "batch/k1",
        {
            "lastMedication": {
                "mode": "medication",
                "start": 10.0,
                "lastUpdated": 10.0,
                "offset": 0.0,
                "medication_id": "m",
                "medication_name": "Med",
                "amount": 0.0,
                "units": "drops",
                "notes": "",
                "_id": "k1",
                "type": "health",
                "isNight": False,
                "multientry_key": "batch",
            }
        },
    ),
    (
        "temperature",
        "health",
        {
            "mode": "temperature",
            "start": 10.0,
            "lastUpdated": 11.0,
            "offset": 0.0,
            "amount": 37.0,
            "units": "C",
            "notes": "n",
        },
        "h3",
        {
            "lastTemperature": {
                "_id": "h3",
                "type": "health",
                "mode": "temperature",
                "start": 10.0,
                "lastUpdated": 11.0,
                "offset": 0.0,
                "amount": 37.0,
                "units": "C",
                "multientry_key": None,
            }
        },
    ),
    (
        "batched temperature",
        "health",
        {"mode": "temperature", "start": 10.0, "offset": 0.0, "amount": 99.0, "units": "F"},
        "batch/k2",
        {
            "lastTemperature": {
                "_id": "k2",
                "type": "health",
                "mode": "temperature",
                "start": 10.0,
                "lastUpdated": 10.0,
                "offset": 0.0,
                "amount": 99.0,
                "units": "F",
                "multientry_key": "batch",
            }
        },
    ),
    (
        "pump",
        "pump",
        {
            "start": 10.0,
            "entryMode": "total",
            "leftAmount": 50.0,
            "rightAmount": 50.0,
            "units": "ml",
            "offset": 0.0,
            "duration": 600.0,
            "end_offset": 0.0,
            "notes": "n",
        },
        "p1",
        {
            "lastPump": {
                "start": 10.0,
                "duration": 600.0,
                "entryMode": "total",
                "leftAmount": 50.0,
                "rightAmount": 50.0,
                "units": "ml",
                "offset": 0.0,
            }
        },
    ),
    (
        "activity",
        "activities",
        {"mode": "tummyTime", "start": 10.0, "offset": 0.0, "duration": 120.0, "end_offset": 0.0, "notes": "n"},
        "a1",
        {"lastTummyTime": {"start": 10.0, "offset": 0.0, "duration": 120.0, "end_offset": 0.0}},
    ),
    (
        "activity without duration",
        "activities",
        {"mode": "bath", "start": 10.0, "offset": 0.0},
        "a2",
        {"lastBath": {"start": 10.0, "offset": 0.0}},
    ),
]


@pytest.mark.parametrize(
    ("tracker", "raw", "entry_id", "expected"),
    [case[1:] for case in SUMMARY_CASES],
    ids=[case[0] for case in SUMMARY_CASES],
)
def test_summary_payloads(tracker: Tracker, raw: dict[str, object], entry_id: str, expected: dict[str, object]) -> None:
    row = _validate_row(tracker, raw)
    assert _summary_payloads(entry_id, row, medication_units="drops") == expected


def test_medication_summary_prefers_row_units_over_carried_units() -> None:
    raw = dict(SUMMARY_CASES[7][2])
    row = _validate_row("health", raw)
    payload = cast(dict[str, object], _summary_payloads("h2", row, medication_units="drops")["lastMedication"])
    assert payload["units"] == "ml"


def _rows(tracker: Tracker, *items: tuple[str, dict[str, object]]) -> list[tuple[str, HistoryRow]]:
    return [(entry_id, _validate_row(tracker, raw)) for entry_id, raw in items]


def test_latest_by_kind_picks_latest_start_per_kind() -> None:
    rows = _rows(
        "feed",
        ("old-bottle", {**BOTTLE_RAW, "start": 50.0}),
        ("new-bottle", {**BOTTLE_RAW, "start": 150.0}),
        ("batch/mid-bottle", {**BOTTLE_RAW, "start": 120.0}),
        ("nursing", {"mode": "breast", "start": 10.0, "lastSide": "left", "offset": 0.0}),
    )
    latest = _latest_by_kind(rows)
    assert {kind: entry_id for kind, (entry_id, _row) in latest.items()} == {
        "lastBottle": "new-bottle",
        "lastNursing": "nursing",
    }


def test_latest_by_kind_separates_diaper_and_potty() -> None:
    rows = _rows(
        "diaper",
        ("diaper", {"mode": "pee", "start": 10.0, "offset": 0.0}),
        ("potty", {"mode": "pee", "start": 20.0, "offset": 0.0, "isPotty": True}),
    )
    assert set(_latest_by_kind(rows)) == {"lastDiaper", "lastPotty"}


def test_summary_updates_rewrites_found_and_removes_missing_kinds() -> None:
    latest = _latest_by_kind(_rows("feed", ("b1", BOTTLE_RAW)))
    updates = _summary_updates({"lastBottle", "lastNursing", "lastSolid"}, latest, None, NOW)
    assert updates == {
        "prefs.lastBottle": _summary_payloads("b1", latest["lastBottle"][1])["lastBottle"],
        "prefs.bottleType": "Formula",
        "prefs.bottleAmount": 120.0,
        "prefs.bottleUnits": "ml",
        "prefs.lastNursing": DELETE_FIELD,
        "prefs.lastSide": DELETE_FIELD,
        "prefs.lastSolid": DELETE_FIELD,
        "prefs.timestamp": {"seconds": NOW},
        "prefs.local_timestamp": NOW,
    }


# ---------------------------------------------------------------------------
# Firestore paths against an in-memory fake (no network)
# ---------------------------------------------------------------------------

CHILD = "child1"


class _Snap:
    def __init__(self, doc_id: str, data: dict[str, object] | None) -> None:
        self.id = doc_id
        self.exists = data is not None
        self._data = data

    def to_dict(self) -> dict[str, object] | None:
        return copy.deepcopy(self._data)


class _FakeQuery:
    """Supports the where/order_by/stream subset that _entries.py uses."""

    def __init__(self, db: _FakeDB, path: tuple[str, ...], filters: tuple = (), order: tuple | None = None) -> None:
        self.db, self.path, self.filters, self.order = db, path, filters, order

    def where(self, *, filter) -> _FakeQuery:
        return _FakeQuery(self.db, self.path, (*self.filters, filter), self.order)

    def order_by(self, field: str, direction: str = "ASCENDING") -> _FakeQuery:
        return _FakeQuery(self.db, self.path, self.filters, (field, direction == "DESCENDING"))

    async def stream(self) -> AsyncIterator[_Snap]:
        ops = {"==": lambda a, b: a == b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b}
        rows = [
            (path[-1], data)
            for path, data in self.db.docs.items()
            if path[:-1] == self.path
            and all(f.field_path in data and ops[f.op_string](data[f.field_path], f.value) for f in self.filters)
            and (self.order is None or self.order[0] in data)
        ]
        if self.order is not None:
            field, descending = self.order
            rows.sort(key=lambda row: row[1][field], reverse=descending)
        for doc_id, data in rows:
            yield _Snap(doc_id, data)


class _FakeDoc:
    def __init__(self, db: _FakeDB, path: tuple[str, ...]) -> None:
        self.db, self.path = db, path

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self.db, (*self.path, name))

    async def get(self, timeout: float | None = None) -> _Snap:
        return _Snap(self.path[-1], self.db.docs.get(self.path))

    async def set(self, data: dict[str, object]) -> None:
        self.db.docs[self.path] = copy.deepcopy(data)

    async def delete(self) -> None:
        self.db.docs.pop(self.path, None)

    async def update(self, updates: dict[str, object]) -> None:
        doc = self.db.docs[self.path]  # KeyError mirrors Firestore NotFound
        for field, value in updates.items():
            *parents, leaf = FieldPath.from_string(field).parts
            target = doc
            for part in parents:
                target = target.setdefault(part, {})
            if value is DELETE_FIELD:
                target.pop(leaf, None)
            else:
                target[leaf] = copy.deepcopy(value)


class _FakeCollection(_FakeQuery):
    def __init__(self, db: _FakeDB, path: tuple[str, ...]) -> None:
        super().__init__(db, path)

    def document(self, doc_id: str) -> _FakeDoc:
        return _FakeDoc(self.db, (*self.path, doc_id))


class _FakeDB:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, ...], dict] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self, (name,))

    def batch(self) -> _FakeBatch:
        return _FakeBatch(self)


class _FakeBatch:
    """Queues writes and applies them all or not at all, like a Firestore write batch."""

    def __init__(self, db: _FakeDB) -> None:
        self.db, self.ops = db, []

    def __getattr__(self, name: str):
        return lambda ref, *args: self.ops.append((getattr(ref, name), args))

    async def commit(self) -> None:
        saved = copy.deepcopy(self.db.docs)
        try:
            for op, args in self.ops:
                await op(*args)
        except BaseException:
            self.db.docs = saved
            raise


def _bottle(start: float, amount: float) -> dict[str, object]:
    return {"mode": "bottle", "start": start, "bottleType": "Formula", "amount": amount, "units": "ml", "offset": 0.0}


FEED = ("feed", CHILD)
INTERVALS = (*FEED, "intervals")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> tuple[HuckleberryAPI, _FakeDB]:
    """API wired to a seeded fake: three bottles (one batched), one nursing, one batched solids."""
    db = _FakeDB()
    db.docs[(*INTERVALS, "b-old")] = _bottle(100.0, 60.0)
    db.docs[(*INTERVALS, "b-new")] = _bottle(300.0, 120.0)
    db.docs[(*INTERVALS, "n1")] = {"mode": "breast", "start": 200.0, "lastSide": "left", "offset": 0.0}
    db.docs[(*INTERVALS, "batch")] = {
        "multi": True,
        "data": {
            "k1": _bottle(250.0, 90.0),
            "k2": {"mode": "solids", "start": 50.0, "offset": 0.0},
        },
    }
    newest = _validate_row("feed", _bottle(300.0, 120.0))
    nursing = _validate_row("feed", db.docs[(*INTERVALS, "n1")])
    db.docs[FEED] = {
        "prefs": {
            **_summary_payloads("b-new", newest),
            **_summary_payloads("n1", nursing),
            "reminderV2": {"mode": "in"},
        }
    }
    api = HuckleberryAPI(email="x", password="x", timezone="UTC", websession=cast(aiohttp.ClientSession, None))
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=db))
    return api, db


def _prefs(db: _FakeDB) -> dict:
    return db.docs[FEED]["prefs"]


async def test_list_entries_returns_ids_including_batched_rows(fake: tuple[HuckleberryAPI, _FakeDB]) -> None:
    api, _db = fake
    start, end = datetime.fromtimestamp(0, timezone.utc), datetime.fromtimestamp(1000, timezone.utc)
    entries = await api.list_entries(CHILD, "feed", start, end)
    assert [entry.id for entry in entries] == ["batch/k2", "b-old", "n1", "batch/k1", "b-new"]
    assert await api.get_entry(CHILD, "feed", "batch/k1") == entries[3]
    assert await api.get_entry(CHILD, "feed", "batch") is None, "a container is not an entry"
    assert await api.get_entry(CHILD, "feed", "b-old/k1") is None


async def test_row_and_summary_writes_are_atomic(fake: tuple[HuckleberryAPI, _FakeDB]) -> None:
    api, db = fake
    before = copy.deepcopy(db.docs)
    with patch.object(_FakeDoc, "update", AsyncMock(side_effect=RuntimeError("summary write failed"))):
        with pytest.raises(RuntimeError):
            await api.delete_entry(CHILD, "feed", "b-new")
        with pytest.raises(RuntimeError):
            await api.update_entry(CHILD, "feed", "b-new", {"amount": 30.0})
    assert db.docs == before


async def test_update_rejects_non_finite_amount(fake: tuple[HuckleberryAPI, _FakeDB]) -> None:
    api, db = fake
    before = copy.deepcopy(db.docs)
    with pytest.raises(ValueError, match="finite"):
        await api.update_entry(CHILD, "feed", "b-new", {"amount": float("inf")})
    assert db.docs == before


async def test_delete_latest_rebuilds_summary_and_bottle_defaults(fake: tuple[HuckleberryAPI, _FakeDB]) -> None:
    api, db = fake
    nursing_before = copy.deepcopy(_prefs(db)["lastNursing"])
    await api.delete_entry(CHILD, "feed", "b-new")
    assert (*INTERVALS, "b-new") not in db.docs
    prefs = _prefs(db)
    assert prefs["lastBottle"]["start"] == 250.0
    assert (prefs["bottleType"], prefs["bottleAmount"], prefs["bottleUnits"]) == ("Formula", 90.0, "ml")
    assert prefs["lastNursing"] == nursing_before
    assert prefs["reminderV2"] == {"mode": "in"}
    assert "seconds" in prefs["timestamp"] and "local_timestamp" in prefs
    with pytest.raises(ValueError, match="No feed entry"):
        await api.delete_entry(CHILD, "feed", "b-new")


async def test_delete_batched_row_removes_only_that_key(fake: tuple[HuckleberryAPI, _FakeDB]) -> None:
    api, db = fake
    await api.delete_entry(CHILD, "feed", "batch/k1")
    assert set(db.docs[(*INTERVALS, "batch")]["data"]) == {"k2"}
    assert _prefs(db)["lastBottle"]["start"] == 300.0


async def test_update_standalone_and_batched_rows(fake: tuple[HuckleberryAPI, _FakeDB]) -> None:
    api, db = fake
    updated = await api.update_entry(CHILD, "feed", "b-old", {"amount": 70})
    assert db.docs[(*INTERVALS, "b-old")]["amount"] == 70
    assert "lastUpdated" in db.docs[(*INTERVALS, "b-old")]
    assert updated.data.model_dump()["amount"] == 70
    assert _prefs(db)["bottleAmount"] == 120.0, "an older row does not take over the defaults"

    await api.update_entry(CHILD, "feed", "batch/k1", {"start": 400.0, "amount": 80.0})
    batch = db.docs[(*INTERVALS, "batch")]["data"]
    assert (batch["k1"]["start"], batch["k1"]["amount"]) == (400.0, 80.0)
    assert batch["k2"] == {"mode": "solids", "start": 50.0, "offset": 0.0}
    assert (_prefs(db)["lastBottle"]["start"], _prefs(db)["bottleAmount"]) == (400.0, 80.0)

    # Moving the newest row back in time hands the summary to the next latest row.
    await api.update_entry(CHILD, "feed", "batch/k1", {"start": 10.0})
    assert _prefs(db)["lastBottle"]["start"] == 300.0


async def test_summary_removed_when_no_row_remains(fake: tuple[HuckleberryAPI, _FakeDB]) -> None:
    api, db = fake
    await api.delete_entry(CHILD, "feed", "n1")
    assert "lastNursing" not in _prefs(db)
    assert "lastSide" not in _prefs(db)

    for entry_id in ("b-new", "batch/k1", "b-old"):
        await api.delete_entry(CHILD, "feed", entry_id)
    prefs = _prefs(db)
    assert "lastBottle" not in prefs
    # With no bottle left the defaults keep the last values written.
    assert (prefs["bottleType"], prefs["bottleAmount"], prefs["bottleUnits"]) == ("Formula", 60.0, "ml")


async def test_invalid_other_row_aborts_before_any_write(fake: tuple[HuckleberryAPI, _FakeDB]) -> None:
    api, db = fake
    db.docs[(*INTERVALS, "bad")] = {"mode": "bottle", "start": 150.0}
    before = copy.deepcopy(db.docs)
    with pytest.raises(ValidationError):
        await api.update_entry(CHILD, "feed", "b-new", {"amount": 1.0})
    with pytest.raises(ValidationError):
        await api.delete_entry(CHILD, "feed", "b-new")
    with pytest.raises(ValueError, match="Invalid value for foods"):
        await api.update_entry(
            CHILD,
            "feed",
            "batch/k2",
            {"foods": {"x": {"id": "x", "created_name": "X", "source": "custom", "bogus": 1}}},
        )
    assert db.docs == before


# ---------------------------------------------------------------------------
# Live integration tests (skip without credentials)
# ---------------------------------------------------------------------------


async def _last_summaries(api: HuckleberryAPI, child_uid: str, tracker: Tracker) -> dict[str, object]:
    """Return the raw prefs.last* fields of a tracker document."""
    db = await api._get_firestore_client()
    snapshot = await db.collection(tracker).document(child_uid).get()
    prefs = (snapshot.to_dict() or {}).get("prefs") or {}
    summaries = {key: value for key, value in prefs.items() if key.startswith("last")}
    # App-written and library-written health summaries differ only in whether they carry `isNight`.
    for value in summaries.values():
        if isinstance(value, dict):
            value.pop("isNight", None)
    return summaries


LogFn = Callable[[HuckleberryAPI, str, datetime], Awaitable[None]]

LIVE_CASES: list[tuple[Tracker, LogFn, dict[str, JsonValue]]] = [
    (
        "sleep",
        lambda api, cid, t: api.log_sleep(cid, start_time=t, end_time=t + timedelta(minutes=5)),
        {"duration": 600},
    ),
    (
        "feed",
        lambda api, cid, t: api.log_bottle(cid, start_time=t, amount=60.0, notes="entries test"),
        {"amount": 90.0, "notes": None},
    ),
    ("diaper", lambda api, cid, t: api.log_diaper(cid, start_time=t, mode="pee"), {"mode": "poo"}),
    (
        "health",
        lambda api, cid, t: api.log_temperature(cid, start_time=t, amount=37.0, units="C"),
        {"amount": 37.5},
    ),
    (
        "pump",
        lambda api, cid, t: api.log_pump(cid, start_time=t, duration=300, total_amount=40.0),
        {"leftAmount": 25.0},
    ),
    (
        "activities",
        lambda api, cid, t: api.log_activity(cid, mode="bath", start_time=t, duration=120),
        {"duration": 180.0},
    ),
]


@pytest.mark.parametrize(("tracker", "log", "changes"), LIVE_CASES, ids=[case[0] for case in LIVE_CASES])
async def test_entry_round_trip(
    api: HuckleberryAPI,
    child_uid: str,
    tracker: Tracker,
    log: LogFn,
    changes: dict[str, JsonValue],
) -> None:
    """Log the newest entry, find, edit, and delete it; prefs.last* must return to their prior values."""
    before = await _last_summaries(api, child_uid, tracker)
    start = datetime.now(timezone.utc).replace(microsecond=0)
    await log(api, child_uid, start)

    entry_id: str | None = None
    try:
        entries = await api.list_entries(child_uid, tracker, start, start + timedelta(seconds=1))
        matches = [entry for entry in entries if float(entry.data.start) == start.timestamp()]
        assert len(matches) == 1
        entry_id = matches[0].id
        assert await api.get_entry(child_uid, tracker, entry_id) == matches[0]

        updated = await api.update_entry(child_uid, tracker, entry_id, changes)
        stored = await api.get_entry(child_uid, tracker, entry_id)
        assert stored == updated
        dumped = updated.data.model_dump(by_alias=True)
        for key, value in changes.items():
            assert dumped[key] == value

        with pytest.raises(ValueError):
            await api.update_entry(child_uid, tracker, entry_id, {"notAField": 1})
    finally:
        if entry_id is not None:
            await api.delete_entry(child_uid, tracker, entry_id)

    assert await api.get_entry(child_uid, tracker, entry_id) is None
    with pytest.raises(ValueError):
        await api.delete_entry(child_uid, tracker, entry_id)
    assert await _last_summaries(api, child_uid, tracker) == before
