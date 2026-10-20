"""Unit tests for explicit start/end times across every live timer."""

from __future__ import annotations

import copy
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any

import aiohttp
import pytest
import pytest_asyncio

from huckleberry_api import HuckleberryAPI

NOW = 1_800_000_000.0
CHILD = "child"


def _event(seconds_ago: float) -> datetime:
    return datetime.fromtimestamp(NOW - seconds_ago, tz=dt_timezone.utc)


class _Snapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


def _set_path(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = target
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = copy.deepcopy(value)


def _merge(target: dict[str, Any], data: dict[str, Any]) -> None:
    for key, value in data.items():
        existing = target.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            _merge(existing, value)
        else:
            target[key] = copy.deepcopy(value)


class _Document:
    def __init__(self, store: _Store, path: str) -> None:
        self._store = store
        self._path = path

    async def get(self, *args: Any, **kwargs: Any) -> _Snapshot:
        return _Snapshot(self._store.documents.get(self._path))

    async def set(self, data: dict[str, Any], merge: bool = False) -> None:
        self._store.writes.append(("set", self._path, data))
        if merge:
            _merge(self._store.documents.setdefault(self._path, {}), data)
        else:
            self._store.documents[self._path] = copy.deepcopy(data)

    async def update(self, data: dict[str, Any]) -> None:
        self._store.writes.append(("update", self._path, data))
        target = self._store.documents.setdefault(self._path, {})
        for dotted, value in data.items():
            _set_path(target, dotted, value)

    def collection(self, name: str) -> _Collection:
        return _Collection(self._store, f"{self._path}/{name}")


class _Collection:
    def __init__(self, store: _Store, path: str) -> None:
        self._store = store
        self._path = path

    def document(self, document_id: str) -> _Document:
        return _Document(self._store, f"{self._path}/{document_id}")


class _Store:
    def __init__(self, documents: dict[str, dict[str, Any]] | None = None) -> None:
        self.documents = copy.deepcopy(documents or {})
        self.writes: list[tuple[str, str, dict[str, Any]]] = []

    def collection(self, name: str) -> _Collection:
        return _Collection(self, name)

    def writes_to(self, path: str) -> list[tuple[str, str, dict[str, Any]]]:
        return [write for write in self.writes if write[1] == path]

    def writes_under(self, prefix: str) -> list[tuple[str, str, dict[str, Any]]]:
        return [write for write in self.writes if write[1].startswith(f"{prefix}/")]


@pytest_asyncio.fixture
async def fixed_now(monkeypatch: pytest.MonkeyPatch) -> float:
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: NOW)
    return NOW


@pytest_asyncio.fixture
async def store(websession: aiohttp.ClientSession, fixed_now: float) -> _Store:
    return _Store()


@pytest_asyncio.fixture
async def api(websession: aiohttp.ClientSession, store: _Store, monkeypatch: pytest.MonkeyPatch) -> HuckleberryAPI:
    api_instance = HuckleberryAPI(email="test@example.com", password="unused", timezone="UTC", websession=websession)

    async def fake_client() -> _Store:
        return store

    async def fake_offset() -> float:
        return 0.0

    monkeypatch.setattr(api_instance, "_get_firestore_client", fake_client)
    monkeypatch.setattr(api_instance, "_get_timezone_offset_minutes", fake_offset)
    return api_instance


class TestSleepExplicitTimes:
    async def test_start_with_time_uses_requested_start(self, api: HuckleberryAPI, store: _Store) -> None:
        start_time = _event(900)

        await api.start_sleep(CHILD, start_time=start_time)

        timer = store.writes_to(f"sleep/{CHILD}")[0][2]["timer"]
        assert timer["timerStartTime"] == pytest.approx(start_time.timestamp() * 1000)
        assert timer["local_timestamp"] == pytest.approx(NOW)

    async def test_start_while_active_moves_start_without_restarting(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"sleep/{CHILD}"] = {
            "timer": {
                "active": True,
                "paused": False,
                "timerStartTime": (NOW - 1200) * 1000,
                "uuid": "keepme",
                "timestamp": {"seconds": NOW - 1200},
                "local_timestamp": NOW - 1200,
            }
        }
        start_time = _event(1500)

        await api.start_sleep(CHILD, start_time=start_time)

        assert len(store.writes) == 1
        kind, path, payload = store.writes[0]
        assert (kind, path) == ("update", f"sleep/{CHILD}")
        assert payload["timer.timerStartTime"] == pytest.approx(start_time.timestamp() * 1000)
        assert "timer.timerStartTime" in payload
        # The session identity is preserved because it is a partial update.
        assert store.documents[f"sleep/{CHILD}"]["timer"]["uuid"] == "keepme"

    async def test_start_while_active_without_time_is_noop(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"sleep/{CHILD}"] = {
            "timer": {"active": True, "paused": False, "timerStartTime": NOW * 1000, "uuid": "keepme"}
        }

        await api.start_sleep(CHILD)

        assert store.writes == []

    async def test_complete_with_start_and_end_overrides(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"sleep/{CHILD}"] = {
            "timer": {"active": True, "paused": False, "timerStartTime": (NOW - 900) * 1000, "uuid": "keepme"}
        }
        start_time = _event(1800)
        end_time = _event(600)

        await api.complete_sleep(CHILD, start_time=start_time, end_time=end_time)

        interval = store.writes_under(f"sleep/{CHILD}/intervals")[0][2]
        assert interval["start"] == int(start_time.timestamp())
        assert interval["duration"] == 1200

    async def test_start_while_paused_rejects_start_after_paused_end(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"sleep/{CHILD}"] = {
            "timer": {
                "active": True,
                "paused": True,
                "timerStartTime": (NOW - 1800) * 1000,
                "timerEndTime": (NOW - 900) * 1000,
                "uuid": "keepme",
            }
        }

        with pytest.raises(ValueError, match="paused sleep end"):
            await api.start_sleep(CHILD, start_time=_event(600))

        assert store.writes == []

    async def test_complete_paused_timer_uses_explicit_end(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"sleep/{CHILD}"] = {
            "timer": {
                "active": True,
                "paused": True,
                "timerStartTime": (NOW - 1800) * 1000,
                "timerEndTime": (NOW - 900) * 1000,
                "uuid": "keepme",
            }
        }

        await api.complete_sleep(CHILD, end_time=_event(600))

        interval = store.writes_under(f"sleep/{CHILD}/intervals")[0][2]
        # The explicit end wins over the paused timer's stored end.
        assert interval["duration"] == 1200

    async def test_complete_rejects_end_before_start_without_writing(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"sleep/{CHILD}"] = {
            "timer": {"active": True, "paused": False, "timerStartTime": (NOW - 300) * 1000, "uuid": "keepme"}
        }

        with pytest.raises(ValueError, match="end_time"):
            await api.complete_sleep(CHILD, end_time=_event(600))

        assert store.writes == []


class TestNursingExplicitTimes:
    async def test_start_with_time_sets_session_and_segment_start(self, api: HuckleberryAPI, store: _Store) -> None:
        start_time = _event(420)

        await api.start_nursing(CHILD, "left", start_time=start_time)

        timer = store.writes_to(f"feed/{CHILD}")[0][2]["timer"]
        assert timer["feedStartTime"] == pytest.approx(start_time.timestamp())
        assert timer["timerStartTime"] == pytest.approx(start_time.timestamp())

    async def test_start_while_active_shifts_start_and_segment(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"feed/{CHILD}"] = {
            "timer": {
                "active": True,
                "paused": False,
                "feedStartTime": NOW - 600,
                "timerStartTime": NOW - 120,
                "leftDuration": 360.0,
                "rightDuration": 0.0,
                "activeSide": "left",
                "uuid": "keepme",
            }
        }
        start_time = _event(900)

        await api.start_nursing(CHILD, start_time=start_time)

        payload = store.writes_to(f"feed/{CHILD}")[0][2]
        assert payload["timer.feedStartTime"] == pytest.approx(start_time.timestamp())
        # The whole timeline shifts by the same delta, so the segment keeps its width.
        assert payload["timer.timerStartTime"] == pytest.approx(start_time.timestamp() + 480)

    async def test_start_while_active_rejects_segment_shift_past_now(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"feed/{CHILD}"] = {
            "timer": {
                "active": True,
                "paused": False,
                "feedStartTime": NOW - 600,
                "timerStartTime": NOW - 120,
                "leftDuration": 360.0,
                "rightDuration": 0.0,
                "activeSide": "left",
                "uuid": "keepme",
            }
        }

        with pytest.raises(ValueError, match="current nursing segment"):
            await api.start_nursing(CHILD, start_time=_event(300))

        assert store.writes == []

    async def test_complete_with_end_time_uses_segment_end(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"feed/{CHILD}"] = {
            "timer": {
                "active": True,
                "paused": False,
                "feedStartTime": NOW - 900,
                "timerStartTime": NOW - 600,
                "leftDuration": 120.0,
                "rightDuration": 0.0,
                "activeSide": "left",
                "lastSide": "left",
                "uuid": "keepme",
            }
        }
        end_time = _event(300)

        await api.complete_nursing(CHILD, end_time=end_time)

        interval = store.writes_under(f"feed/{CHILD}/intervals")[0][2]
        assert interval["start"] == pytest.approx(NOW - 900)
        # 120s accumulated + 300s from the current segment.
        assert interval["leftDuration"] == pytest.approx(420.0)


class TestPumpExplicitTimes:
    async def test_start_with_time_uses_requested_start(self, api: HuckleberryAPI, store: _Store) -> None:
        start_time = _event(300)

        await api.start_pump(CHILD, start_time=start_time)

        timer = store.writes_to(f"pump/{CHILD}")[0][2]["timer"]
        assert timer["startTime"] == pytest.approx(start_time.timestamp() * 1000)

    async def test_complete_with_start_and_end_overrides(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"pump/{CHILD}"] = {
            "timer": {"active": True, "paused": False, "startTime": (NOW - 900) * 1000, "uuid": "keepme"}
        }
        start_time = _event(1200)
        end_time = _event(600)

        await api.complete_pump(CHILD, start_time=start_time, end_time=end_time, total_amount=100)

        interval = store.writes_under(f"pump/{CHILD}/intervals")[0][2]
        assert interval["start"] == pytest.approx(start_time.timestamp())
        assert interval["duration"] == pytest.approx(600)

    async def test_complete_rejects_end_before_start_without_writing(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"pump/{CHILD}"] = {
            "timer": {"active": True, "paused": False, "startTime": (NOW - 300) * 1000, "uuid": "keepme"}
        }

        with pytest.raises(ValueError, match="end_time"):
            await api.complete_pump(CHILD, end_time=_event(600), total_amount=100)

        assert store.writes == []


class TestActivityExplicitTimes:
    async def test_start_with_time_uses_requested_start(self, api: HuckleberryAPI, store: _Store) -> None:
        start_time = _event(300)

        await api.start_activity(CHILD, "bath", start_time=start_time)

        timer = store.writes_to(f"activities/{CHILD}")[0][2]["timer"]["bath"]
        assert timer["startTime"] == pytest.approx(start_time.timestamp() * 1000)

    async def test_complete_with_end_time_derives_duration(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"activities/{CHILD}"] = {
            "timer": {"bath": {"active": True, "paused": False, "startTime": (NOW - 900) * 1000, "uuid": "keepme"}}
        }
        end_time = _event(300)

        await api.complete_activity(CHILD, "bath", end_time=end_time)

        interval = store.writes_under(f"activities/{CHILD}/intervals")[0][2]
        assert interval["start"] == pytest.approx(NOW - 900)
        assert interval["duration"] == pytest.approx(600)

    async def test_complete_rejects_end_and_duration_together(self, api: HuckleberryAPI, store: _Store) -> None:
        store.documents[f"activities/{CHILD}"] = {
            "timer": {"bath": {"active": True, "paused": False, "startTime": (NOW - 900) * 1000, "uuid": "keepme"}}
        }

        with pytest.raises(ValueError, match="either end_time or duration"):
            await api.complete_activity(CHILD, "bath", end_time=_event(300), duration=120)

        assert store.writes == []
