"""Unit tests for body-temperature tracking."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

from huckleberry_api import HuckleberryAPI


class _Snapshot:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, object]:
        return self._payload


class _DataDocument:
    def __init__(self, writes: list[dict[str, object]]) -> None:
        self._writes = writes

    async def set(self, payload: dict[str, object]) -> None:
        self._writes.append(payload)


class _DataCollection:
    def __init__(self, writes: list[dict[str, object]]) -> None:
        self._writes = writes

    def document(self, _document_id: str) -> _DataDocument:
        return _DataDocument(self._writes)


class _HealthDocument:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.data_writes: list[dict[str, object]] = []
        self.updates: list[dict[str, object]] = []

    async def get(self) -> _Snapshot:
        return _Snapshot(self._payload)

    def collection(self, name: str) -> _DataCollection:
        assert name == "data"
        return _DataCollection(self.data_writes)

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

    await api.log_temperature("child", start_time=measured_at, amount=38.2, units="C")

    assert len(health_document.data_writes) == 1
    written = health_document.data_writes[0]
    assert written["type"] == "health"
    assert written["mode"] == "temperature"
    assert written["start"] == measured_at.timestamp()
    assert written["amount"] == 38.2
    assert written["units"] == "C"
    assert health_document.updates[0]["prefs.lastTemperature"] == written


async def test_log_temperature_does_not_replace_newer_latest_pref(websession, monkeypatch) -> None:
    health_document = _HealthDocument(
        {
            "prefs": {
                "lastTemperature": {
                    "mode": "temperature",
                    "start": datetime(2026, 8, 23, tzinfo=timezone.utc).timestamp(),
                    "offset": 0,
                    "amount": 99.1,
                    "units": "F",
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
