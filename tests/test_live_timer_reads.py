"""Unit tests for typed live timer reads."""

from unittest.mock import AsyncMock

from huckleberry_api import HuckleberryAPI


class _Snapshot:
    exists = True

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, object]:
        return self._payload


class _Document:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    async def get(self, *, timeout: float) -> _Snapshot:
        assert timeout == 10.0
        return _Snapshot(self._payload)


class _Collection:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def document(self, child_uid: str) -> _Document:
        assert child_uid == "child"
        return _Document(self._payload)


class _Client:
    def __init__(self, payloads: dict[str, dict[str, object]]) -> None:
        self._payloads = payloads

    def collection(self, name: str) -> _Collection:
        return _Collection(self._payloads[name])


async def test_get_sleep_returns_active_typed_timer(websession, monkeypatch) -> None:
    client = _Client(
        {
            "sleep": {
                "timer": {
                    "active": True,
                    "paused": False,
                    "timerStartTime": 1_789_476_289_251.0,
                    "uuid": "sleep-session",
                }
            }
        }
    )
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=client))

    sleep = await api.get_sleep("child")

    assert sleep is not None and sleep.timer is not None
    assert sleep.timer.active is True
    assert sleep.timer.paused is False
    assert sleep.timer.timerStartTime == 1_789_476_289_251.0
    assert sleep.timer.uuid == "sleep-session"


async def test_get_nursing_returns_active_typed_timer(websession, monkeypatch) -> None:
    client = _Client(
        {
            "feed": {
                "timer": {
                    "active": True,
                    "paused": False,
                    "feedStartTime": 1_789_476_624.0,
                    "timerStartTime": 1_789_476_624.0,
                    "uuid": "nursing-session",
                    "leftDuration": 12.0,
                    "rightDuration": 4.0,
                    "lastSide": "left",
                    "activeSide": "right",
                }
            }
        }
    )
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=client))

    nursing = await api.get_nursing("child")

    assert nursing is not None and nursing.timer is not None
    assert nursing.timer.active is True
    assert nursing.timer.paused is False
    assert nursing.timer.feedStartTime == 1_789_476_624.0
    assert nursing.timer.activeSide == "right"
    assert nursing.timer.lastSide == "left"
