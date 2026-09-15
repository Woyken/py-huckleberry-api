"""Unit tests for body-temperature tracking."""

from datetime import datetime, timezone
from typing import cast
from unittest.mock import AsyncMock

import pytest
from google.api_core.exceptions import GoogleAPICallError
from google.cloud import firestore

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import FirebaseTemperatureData, TemperatureUnits


class _Snapshot:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, object]:
        return self._payload


class _DataDocument:
    def __init__(self, writes: list[dict[str, object]], write_error: GoogleAPICallError | None) -> None:
        self._writes = writes
        self._write_error = write_error

    async def set(self, payload: dict[str, object]) -> None:
        if self._write_error is not None:
            raise self._write_error
        self._writes.append(payload)


class _DataCollection:
    def __init__(self, writes: list[dict[str, object]], write_error: GoogleAPICallError | None) -> None:
        self._writes = writes
        self._write_error = write_error

    def document(self, _document_id: str) -> _DataDocument:
        return _DataDocument(self._writes, self._write_error)


class _HealthDocument:
    def __init__(self, payload: dict[str, object], write_error: GoogleAPICallError | None = None) -> None:
        self._payload = payload
        self._write_error = write_error
        self.data_writes: list[dict[str, object]] = []
        self.updates: list[dict[str, object]] = []

    async def get(self) -> _Snapshot:
        return _Snapshot(self._payload)

    def collection(self, name: str) -> _DataCollection:
        assert name == "data"
        return _DataCollection(self.data_writes, self._write_error)

    async def update(self, payload: dict[str, object]) -> None:
        self.updates.append(payload)


class _HealthCollection:
    def __init__(self, document: _HealthDocument) -> None:
        self._document = document

    def document(self, _child_uid: str) -> _HealthDocument:
        return self._document


class _Client:
    def __init__(self, document: _HealthDocument) -> None:
        self._document = document

    def collection(self, name: str) -> _HealthCollection:
        assert name == "health"
        return _HealthCollection(self._document)


async def test_log_temperature_writes_health_data_and_latest_pref(websession, monkeypatch) -> None:
    health_document = _HealthDocument({})
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=_Client(health_document)))
    monkeypatch.setattr(api, "_get_timezone_offset_minutes", AsyncMock(return_value=0))
    measured_at = datetime(2026, 8, 22, 18, 30, tzinfo=timezone.utc)

    await api.log_temperature("child", start_time=measured_at, amount=38.2, units="C", notes="Fever")

    assert len(health_document.data_writes) == 1
    written = health_document.data_writes[0]
    assert written == {
        "mode": "temperature",
        "start": measured_at.timestamp(),
        "lastUpdated": written["lastUpdated"],
        "offset": 0,
        "amount": 38.2,
        "units": "C",
        "notes": "Fever",
    }

    latest = cast(dict[str, object], health_document.updates[0]["prefs.lastTemperature"])
    assert latest == {
        "_id": latest["_id"],
        "type": "health",
        "mode": "temperature",
        "start": measured_at.timestamp(),
        "lastUpdated": written["lastUpdated"],
        "offset": 0,
        "amount": 38.2,
        "units": "C",
        "multientry_key": None,
    }


async def test_log_temperature_does_not_replace_newer_latest_pref(websession, monkeypatch) -> None:
    health_document = _HealthDocument(
        {
            "prefs": {
                "lastTemperature": {
                    "_id": "newer-temperature",
                    "type": "health",
                    "mode": "temperature",
                    "start": datetime(2026, 8, 23, tzinfo=timezone.utc).timestamp(),
                    "lastUpdated": datetime(2026, 8, 23, tzinfo=timezone.utc).timestamp(),
                    "offset": 0,
                    "amount": 99.1,
                    "units": "F",
                    "multientry_key": None,
                }
            }
        }
    )
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=_Client(health_document)))
    monkeypatch.setattr(api, "_get_timezone_offset_minutes", AsyncMock(return_value=0))

    await api.log_temperature(
        "child",
        start_time=datetime(2026, 8, 22, tzinfo=timezone.utc),
        amount=98.6,
        units="F",
    )

    assert len(health_document.data_writes) == 1
    assert health_document.updates == []


async def test_log_temperature_propagates_history_write_failure(websession, monkeypatch) -> None:
    health_document = _HealthDocument({}, write_error=GoogleAPICallError("write failed"))
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=_Client(health_document)))
    monkeypatch.setattr(api, "_get_timezone_offset_minutes", AsyncMock(return_value=0))

    with pytest.raises(GoogleAPICallError, match="write failed"):
        await api.log_temperature(
            "child",
            start_time=datetime(2026, 8, 22, tzinfo=timezone.utc),
            amount=37.0,
            units="C",
        )

    assert health_document.updates == []


@pytest.mark.integration
async def test_log_temperature_celsius_and_fahrenheit_live(api: HuckleberryAPI, child_uid: str) -> None:
    db = await api._get_firestore_client()
    health_ref = db.collection("health").document(child_uid)
    health_snapshot = await health_ref.get()
    health_data = health_snapshot.to_dict() or {}
    latest = health_data.get("prefs", {}).get("lastTemperature")
    test_cases: list[tuple[datetime, float, TemperatureUnits, str | None]] = [
        (datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc), 37.2, "C", "Celsius integration test"),
        (datetime(2020, 1, 2, 3, 4, 6, tzinfo=timezone.utc), 98.6, "F", None),
    ]
    if not latest or latest["start"] <= test_cases[-1][0].timestamp():
        pytest.skip("A newer lastTemperature is required for non-destructive integration testing")

    try:
        for start_time, amount, units, notes in test_cases:
            await api.log_temperature(
                child_uid,
                start_time=start_time,
                amount=amount,
                units=units,
                notes=notes,
            )
            query = health_ref.collection("data").where(
                filter=firestore.FieldFilter("start", "==", start_time.timestamp())
            )
            snapshots = await query.get()
            assert len(snapshots) == 1
            entry = FirebaseTemperatureData.model_validate(snapshots[0].to_dict())
            assert entry.amount == amount
            assert entry.units == units
            assert entry.notes == notes
    finally:
        for start_time, _, _, _ in test_cases:
            query = health_ref.collection("data").where(
                filter=firestore.FieldFilter("start", "==", start_time.timestamp())
            )
            for snapshot in await query.get():
                await snapshot.reference.delete()
