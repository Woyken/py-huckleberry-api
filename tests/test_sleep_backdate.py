"""Backdating tests for sleep and bottle logging.

These run against an in-memory Firestore double so they exercise the exact
documents written, without needing live credentials.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any, cast

import aiohttp
import pytest
import pytest_asyncio
from google.cloud.firestore_v1 import AsyncClient

from huckleberry_api import HuckleberryAPI

CHILD_UID = "child-under-test"
SLEEP_PATH = f"sleep/{CHILD_UID}"
FEED_PATH = f"feed/{CHILD_UID}"


class FakeDocumentSnapshot:
    """Minimal stand-in for a Firestore document snapshot."""

    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


class FakeCollectionReference:
    """Minimal stand-in for a Firestore collection reference."""

    def __init__(self, store: FakeFirestore, path: str) -> None:
        self._store = store
        self.path = path

    def document(self, document_id: str) -> FakeDocumentReference:
        return FakeDocumentReference(self._store, f"{self.path}/{document_id}")


class FakeDocumentReference:
    """Minimal stand-in for a Firestore document reference that records writes."""

    def __init__(self, store: FakeFirestore, path: str) -> None:
        self._store = store
        self.path = path

    def collection(self, collection_id: str) -> FakeCollectionReference:
        return FakeCollectionReference(self._store, f"{self.path}/{collection_id}")

    async def get(self, timeout: float | None = None) -> FakeDocumentSnapshot:
        return FakeDocumentSnapshot(self._store.documents.get(self.path))

    async def set(self, data: dict[str, Any], merge: bool = False) -> None:
        self._store.writes.append(("set", self.path, data))

    async def update(self, data: dict[str, Any]) -> None:
        self._store.writes.append(("update", self.path, data))


class FakeFirestore:
    """In-memory Firestore double recording every write."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.writes: list[tuple[str, str, dict[str, Any]]] = []

    def collection(self, collection_id: str) -> FakeCollectionReference:
        return FakeCollectionReference(self, collection_id)

    def writes_to(self, path: str) -> list[tuple[str, str, dict[str, Any]]]:
        """Return recorded writes whose document path matches exactly."""
        return [write for write in self.writes if write[1] == path]

    def interval_writes(self, document_path: str) -> list[dict[str, Any]]:
        """Return payloads written to the intervals subcollection of a document."""
        prefix = f"{document_path}/intervals/"
        return [write[2] for write in self.writes if write[1].startswith(prefix)]


def active_timer_document(
    timer_start_ms: float, *, paused: bool = False, timer_end_ms: float | None = None
) -> dict[str, Any]:
    """Build a sleep document with a running (or paused) timer."""
    timer: dict[str, Any] = {
        "active": True,
        "paused": paused,
        "timerStartTime": timer_start_ms,
        "uuid": "0123456789abcdef",
        "timestamp": {"seconds": timer_start_ms / 1000},
        "local_timestamp": timer_start_ms / 1000,
    }
    if timer_end_ms is not None:
        timer["timerEndTime"] = timer_end_ms
    return {"timer": timer}


@pytest_asyncio.fixture
async def fake_db() -> FakeFirestore:
    """Fresh in-memory Firestore double."""
    return FakeFirestore()


@pytest_asyncio.fixture
async def offline_api(
    websession: aiohttp.ClientSession, fake_db: FakeFirestore, monkeypatch: pytest.MonkeyPatch
) -> HuckleberryAPI:
    """API client wired to the Firestore double instead of a live project."""
    api_instance = HuckleberryAPI(email="test@example.com", password="not-used", timezone="UTC", websession=websession)

    async def fake_get_firestore_client() -> AsyncClient:
        return cast(AsyncClient, fake_db)

    async def fake_get_timezone_offset_minutes() -> float:
        return 0.0

    monkeypatch.setattr(api_instance, "_get_firestore_client", fake_get_firestore_client)
    monkeypatch.setattr(api_instance, "_get_timezone_offset_minutes", fake_get_timezone_offset_minutes)
    return api_instance


class TestStartSleepBackdating:
    """start_sleep with an explicit start_time."""

    async def test_start_time_sets_timer_start_and_keeps_timestamps_now(
        self, offline_api: HuckleberryAPI, fake_db: FakeFirestore
    ) -> None:
        """timerStartTime follows start_time while the timestamps stay at now."""
        start_time = datetime.now(dt_timezone.utc) - timedelta(minutes=15)
        before = time.time()

        await offline_api.start_sleep(CHILD_UID, start_time=start_time)

        after = time.time()
        writes = fake_db.writes_to(SLEEP_PATH)
        assert len(writes) == 1
        timer = writes[0][2]["timer"]

        assert timer["timerStartTime"] == pytest.approx(start_time.timestamp() * 1000)
        # "last modified" markers are now, not the backdated start.
        assert before <= timer["local_timestamp"] <= after
        assert before <= timer["timestamp"]["seconds"] <= after
        assert timer["active"] is True
        assert timer["paused"] is False

    async def test_omitting_start_time_uses_now(self, offline_api: HuckleberryAPI, fake_db: FakeFirestore) -> None:
        """Existing callers keep the previous behaviour."""
        before = time.time()

        await offline_api.start_sleep(CHILD_UID)

        after = time.time()
        timer = fake_db.writes_to(SLEEP_PATH)[0][2]["timer"]
        assert before * 1000 <= timer["timerStartTime"] <= after * 1000

    async def test_naive_start_time_raises_without_writing(
        self, offline_api: HuckleberryAPI, fake_db: FakeFirestore
    ) -> None:
        """A naive datetime is rejected before any Firestore write."""
        with pytest.raises(ValueError, match="timezone-aware"):
            await offline_api.start_sleep(CHILD_UID, start_time=datetime.now())

        assert fake_db.writes == []

    async def test_future_start_time_raises_without_writing(
        self, offline_api: HuckleberryAPI, fake_db: FakeFirestore
    ) -> None:
        """A start time beyond the skew allowance is rejected before writing."""
        future = datetime.now(dt_timezone.utc) + timedelta(minutes=5)

        with pytest.raises(ValueError, match="future"):
            await offline_api.start_sleep(CHILD_UID, start_time=future)

        assert fake_db.writes == []

    async def test_slightly_future_start_time_is_allowed(
        self, offline_api: HuckleberryAPI, fake_db: FakeFirestore
    ) -> None:
        """Small clock skew stays within the 60 second allowance."""
        nearly_now = datetime.now(dt_timezone.utc) + timedelta(seconds=30)

        await offline_api.start_sleep(CHILD_UID, start_time=nearly_now)

        assert len(fake_db.writes_to(SLEEP_PATH)) == 1


class TestCompleteSleepBackdating:
    """complete_sleep with an explicit end_time."""

    async def test_end_time_drives_interval_duration(
        self, offline_api: HuckleberryAPI, fake_db: FakeFirestore
    ) -> None:
        """The stored interval ends at end_time, not at now."""
        start_time = datetime.now(dt_timezone.utc) - timedelta(minutes=70)
        end_time = datetime.now(dt_timezone.utc) - timedelta(minutes=10)
        fake_db.documents[SLEEP_PATH] = active_timer_document(start_time.timestamp() * 1000)

        await offline_api.complete_sleep(CHILD_UID, end_time=end_time)

        intervals = fake_db.interval_writes(SLEEP_PATH)
        assert len(intervals) == 1
        interval = intervals[0]
        assert interval["start"] == int(start_time.timestamp())
        assert interval["duration"] == pytest.approx(60 * 60, abs=2)

        reset = fake_db.writes_to(SLEEP_PATH)[0][2]
        assert reset["timer"]["active"] is False
        assert reset["prefs.lastSleep"]["duration"] == interval["duration"]

    async def test_omitting_end_time_uses_now(self, offline_api: HuckleberryAPI, fake_db: FakeFirestore) -> None:
        """Existing callers keep the previous behaviour."""
        start_time = datetime.now(dt_timezone.utc) - timedelta(minutes=30)
        fake_db.documents[SLEEP_PATH] = active_timer_document(start_time.timestamp() * 1000)

        await offline_api.complete_sleep(CHILD_UID)

        assert fake_db.interval_writes(SLEEP_PATH)[0]["duration"] == pytest.approx(30 * 60, abs=2)

    async def test_end_time_before_start_raises_without_writing(
        self, offline_api: HuckleberryAPI, fake_db: FakeFirestore
    ) -> None:
        """An end before the start is rejected and nothing is written."""
        start_time = datetime.now(dt_timezone.utc) - timedelta(minutes=10)
        fake_db.documents[SLEEP_PATH] = active_timer_document(start_time.timestamp() * 1000)
        too_early = start_time - timedelta(minutes=5)

        with pytest.raises(ValueError, match="after the sleep start"):
            await offline_api.complete_sleep(CHILD_UID, end_time=too_early)

        assert fake_db.writes == []

    async def test_future_end_time_raises_without_writing(
        self, offline_api: HuckleberryAPI, fake_db: FakeFirestore
    ) -> None:
        """An end time beyond the skew allowance is rejected before reading or writing."""
        fake_db.documents[SLEEP_PATH] = active_timer_document(time.time() * 1000)

        with pytest.raises(ValueError, match="future"):
            await offline_api.complete_sleep(CHILD_UID, end_time=datetime.now(dt_timezone.utc) + timedelta(hours=1))

        assert fake_db.writes == []

    async def test_naive_end_time_raises_without_writing(
        self, offline_api: HuckleberryAPI, fake_db: FakeFirestore
    ) -> None:
        """A naive end time is rejected before any Firestore write."""
        fake_db.documents[SLEEP_PATH] = active_timer_document(time.time() * 1000)

        with pytest.raises(ValueError, match="timezone-aware"):
            await offline_api.complete_sleep(CHILD_UID, end_time=datetime.now())

        assert fake_db.writes == []

    async def test_paused_timer_ignores_end_time(
        self, offline_api: HuckleberryAPI, fake_db: FakeFirestore, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A paused timer keeps timerEndTime and warns that end_time was ignored."""
        start_time = datetime.now(dt_timezone.utc) - timedelta(minutes=45)
        paused_end = datetime.now(dt_timezone.utc) - timedelta(minutes=15)
        fake_db.documents[SLEEP_PATH] = active_timer_document(
            start_time.timestamp() * 1000, paused=True, timer_end_ms=paused_end.timestamp() * 1000
        )

        with caplog.at_level("WARNING"):
            await offline_api.complete_sleep(CHILD_UID, end_time=datetime.now(dt_timezone.utc) - timedelta(minutes=1))

        # 45 - 15 = 30 minutes, from timerEndTime rather than the supplied end_time.
        assert fake_db.interval_writes(SLEEP_PATH)[0]["duration"] == pytest.approx(30 * 60, abs=2)
        assert "Ignoring end_time" in caplog.text


class TestSetSleepStartTime:
    """set_sleep_start_time adjusts a running timer."""

    async def test_updates_running_timer(self, offline_api: HuckleberryAPI, fake_db: FakeFirestore) -> None:
        """The running timer's start moves and last-modified markers refresh."""
        fake_db.documents[SLEEP_PATH] = active_timer_document(time.time() * 1000)
        new_start = datetime.now(dt_timezone.utc) - timedelta(minutes=20)
        before = time.time()

        await offline_api.set_sleep_start_time(CHILD_UID, new_start)

        after = time.time()
        writes = fake_db.writes_to(SLEEP_PATH)
        assert len(writes) == 1
        kind, _path, payload = writes[0]
        assert kind == "update"
        assert payload["timer.timerStartTime"] == pytest.approx(new_start.timestamp() * 1000)
        assert before <= payload["timer.local_timestamp"] <= after
        assert before <= payload["timer.timestamp"]["seconds"] <= after

    async def test_inactive_timer_raises(self, offline_api: HuckleberryAPI, fake_db: FakeFirestore) -> None:
        """An inactive timer cannot be adjusted."""
        document = active_timer_document(time.time() * 1000)
        document["timer"]["active"] = False
        fake_db.documents[SLEEP_PATH] = document

        with pytest.raises(ValueError, match="no active sleep"):
            await offline_api.set_sleep_start_time(CHILD_UID, datetime.now(dt_timezone.utc) - timedelta(minutes=5))

        assert fake_db.writes == []

    async def test_missing_document_raises(self, offline_api: HuckleberryAPI, fake_db: FakeFirestore) -> None:
        """A child with no sleep document cannot be adjusted."""
        with pytest.raises(ValueError, match="no active sleep"):
            await offline_api.set_sleep_start_time(CHILD_UID, datetime.now(dt_timezone.utc) - timedelta(minutes=5))

        assert fake_db.writes == []

    async def test_naive_start_time_raises_without_reading(
        self, offline_api: HuckleberryAPI, fake_db: FakeFirestore
    ) -> None:
        """Validation happens before the document is touched."""
        fake_db.documents[SLEEP_PATH] = active_timer_document(time.time() * 1000)

        with pytest.raises(ValueError, match="timezone-aware"):
            await offline_api.set_sleep_start_time(CHILD_UID, datetime.now())

        assert fake_db.writes == []

    async def test_future_start_time_raises(self, offline_api: HuckleberryAPI, fake_db: FakeFirestore) -> None:
        """A start time beyond the skew allowance is rejected."""
        fake_db.documents[SLEEP_PATH] = active_timer_document(time.time() * 1000)

        with pytest.raises(ValueError, match="future"):
            await offline_api.set_sleep_start_time(CHILD_UID, datetime.now(dt_timezone.utc) + timedelta(minutes=10))

        assert fake_db.writes == []


class TestLogBottleBackdating:
    """log_bottle already accepts start_time; Part 2 depends on it landing verbatim."""

    async def test_start_matches_supplied_time(self, offline_api: HuckleberryAPI, fake_db: FakeFirestore) -> None:
        """The interval's `start` equals the supplied time, not now."""
        start_time = datetime.now(dt_timezone.utc) - timedelta(minutes=25)

        await offline_api.log_bottle(CHILD_UID, start_time=start_time, amount=120.0)

        intervals = fake_db.interval_writes(FEED_PATH)
        assert len(intervals) == 1
        entry = intervals[0]
        assert entry["start"] == pytest.approx(start_time.timestamp())
        assert entry["mode"] == "bottle"
        assert entry["amount"] == 120.0

        prefs_writes = fake_db.writes_to(FEED_PATH)
        assert prefs_writes[0][2]["prefs"]["lastBottle"]["start"] == pytest.approx(start_time.timestamp())
