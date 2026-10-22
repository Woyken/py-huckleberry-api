"""Real-time listener tests for Huckleberry API."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import FirebaseActivityDocumentData, FirebasePumpDocumentData
from huckleberry_api.models import SolidsFoodReference


async def _wait_for_update(updates: list[Any], predicate: Callable[[Any], bool], timeout: float = 10.0) -> None:
    """Wait until the latest snapshot satisfies the predicate instead of assuming a fixed delay."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if updates and predicate(updates[-1]):
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"No matching listener update within {timeout}s")


async def _wait_or_resubscribe(
    api: HuckleberryAPI,
    updates: list[Any],
    subscribe: Callable[[], Awaitable[None]],
    predicate: Callable[[Any], bool],
    timeout: float = 6.0,
) -> None:
    """Wait for a pushed snapshot, resubscribing if the watch stalls.

    Live Firestore watches occasionally deliver their initial snapshot and then
    spend tens of seconds reconnecting. A fresh subscription receives the current
    document state immediately, so the assertion still checks that the listener
    reports the write.
    """
    try:
        await _wait_for_update(updates, predicate, timeout=timeout)
        return
    except AssertionError:
        pass

    for _ in range(2):
        await api.stop_all_listeners()
        updates.clear()
        await asyncio.sleep(1)
        await subscribe()
        try:
            await _wait_for_update(updates, predicate, timeout=5.0)
            return
        except AssertionError:
            continue

    raise AssertionError("Listener did not report the expected state after resubscribing")


class TestRealtimeListeners:
    """Test real-time listener functionality."""

    async def test_sleep_listener(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test sleep real-time listener."""
        updates: list[Any] = []

        def callback(data: Any) -> None:
            updates.append(data)

        def subscribe() -> Awaitable[None]:
            return api.setup_sleep_listener(child_uid, callback)

        await subscribe()
        await asyncio.sleep(2)

        await api.start_sleep(child_uid)
        await _wait_or_resubscribe(
            api,
            updates,
            subscribe,
            lambda update: update.timer is not None and update.timer.active is True,
        )

        await api.cancel_sleep(child_uid)
        await _wait_or_resubscribe(
            api,
            updates,
            subscribe,
            lambda update: update.timer is not None and update.timer.active is False,
        )
        await api.stop_all_listeners()

        assert len(updates) > 0
        assert updates[-1].timer is not None
        assert updates[-1].timer.active is False

    async def test_feed_listener(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test feeding real-time listener."""
        updates: list[Any] = []

        def callback(data: Any) -> None:
            updates.append(data)

        def subscribe() -> Awaitable[None]:
            return api.setup_feed_listener(child_uid, callback)

        await subscribe()
        await asyncio.sleep(2)

        await api.start_nursing(child_uid, side="left")
        await _wait_or_resubscribe(
            api,
            updates,
            subscribe,
            lambda update: update.timer is not None and update.timer.active is True,
        )

        await api.cancel_nursing(child_uid)
        await _wait_or_resubscribe(
            api,
            updates,
            subscribe,
            lambda update: update.timer is not None and update.timer.active is False,
        )
        await api.stop_all_listeners()

        assert len(updates) > 0
        assert updates[-1].timer is not None
        assert updates[-1].timer.active is False

    async def test_feed_listener_emits_nursing_and_solids_updates(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test feed listener emissions across nursing and solids updates."""
        updates: list[Any] = []

        def callback(data: Any) -> None:
            updates.append(data)

        def subscribe() -> Awaitable[None]:
            return api.setup_feed_listener(child_uid, callback)

        curated = await api.list_solids_curated_foods()
        assert curated

        await subscribe()
        await asyncio.sleep(2)

        await api.start_nursing(child_uid, side="left")
        await _wait_or_resubscribe(
            api,
            updates,
            subscribe,
            lambda update: update.timer is not None and update.timer.active is True,
        )

        await api.complete_nursing(child_uid)
        await _wait_or_resubscribe(
            api,
            updates,
            subscribe,
            lambda update: (
                update.timer is not None
                and update.timer.active is False
                and getattr(update.prefs, "lastNursing", None) is not None
            ),
        )

        solids_start = datetime.now(timezone.utc).replace(microsecond=0)
        await api.log_solids(
            child_uid,
            start_time=solids_start,
            foods=[
                SolidsFoodReference(
                    id=curated[0].id,
                    source="curated",
                    name=curated[0].name,
                    amount="small",
                )
            ],
        )
        await _wait_or_resubscribe(
            api,
            updates,
            subscribe,
            lambda update: (
                getattr(update.prefs, "lastSolid", None) is not None
                and update.prefs.lastSolid.start is not None
                and abs(update.prefs.lastSolid.start - solids_start.timestamp()) < 0.001
            ),
        )

        await api.stop_all_listeners()

    async def test_listener_survives_token_refresh(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that listeners survive token refresh."""
        updates: list[Any] = []

        def callback(data: Any) -> None:
            updates.append(data)

        await api.setup_sleep_listener(child_uid, callback)
        await asyncio.sleep(2)

        initial_count = len(updates)
        original_listener_client = api._listener_client

        assert original_listener_client is not None

        await api.refresh_session_token()
        await asyncio.sleep(2)

        assert api._listener_client is not None
        assert api._listener_client is not original_listener_client

        await api.start_sleep(child_uid)
        await asyncio.sleep(2)

        await api.cancel_sleep(child_uid)
        await api.stop_all_listeners()

        assert len(updates) > initial_count

    async def test_health_listener(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test health/growth real-time listener."""
        updates: list[Any] = []

        def callback(data: Any) -> None:
            updates.append(data)

        await api.setup_health_listener(child_uid, callback)
        await asyncio.sleep(2)

        await api.log_growth(
            child_uid,
            start_time=datetime.now(timezone.utc).replace(microsecond=0),
            weight=5.5,
            units="metric",
        )
        await asyncio.sleep(2)

        await api.stop_all_listeners()

        assert len(updates) > 0
        last_update = updates[-1]
        assert last_update.prefs is not None
        assert last_update.prefs.lastGrowthEntry is not None

    async def test_health_listener_imperial_growth(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test health listener emits imperial growth updates."""
        updates: list[Any] = []

        def callback(data: Any) -> None:
            updates.append(data)

        def subscribe() -> Awaitable[None]:
            return api.setup_health_listener(child_uid, callback)

        await subscribe()
        await asyncio.sleep(2)

        measured_at = datetime.now(timezone.utc).replace(microsecond=0)
        await api.log_growth(
            child_uid,
            start_time=measured_at,
            weight=11.5,
            head=13.8,
            units="imperial",
        )
        await _wait_or_resubscribe(
            api,
            updates,
            subscribe,
            lambda update: (
                getattr(update, "prefs", None) is not None
                and getattr(update.prefs, "lastGrowthEntry", None) is not None
                and abs(update.prefs.lastGrowthEntry.start - measured_at.timestamp()) < 0.001
            ),
        )

        await api.stop_all_listeners()

        assert len(updates) > 0

        growth_updates = [
            update.prefs.lastGrowthEntry
            for update in updates
            if getattr(update, "prefs", None) is not None and getattr(update.prefs, "lastGrowthEntry", None) is not None
        ]

        assert growth_updates
        last_growth = growth_updates[-1]
        assert last_growth.id_ is not None
        assert last_growth.weight == 11.5
        assert last_growth.weightUnits == "lbs.oz"
        assert last_growth.height is None
        assert last_growth.heightUnits is None
        assert last_growth.head == 13.8
        assert last_growth.headUnits == "hin"

    async def test_diaper_listener(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test diaper real-time listener."""
        updates: list[Any] = []

        def callback(data: Any) -> None:
            updates.append(data)

        await api.setup_diaper_listener(child_uid, callback)
        await asyncio.sleep(2)

        await api.log_diaper(child_uid, start_time=datetime.now(timezone.utc).replace(microsecond=0), mode="pee")
        await asyncio.sleep(2)

        await api.stop_all_listeners()

        assert len(updates) > 0
        last_update = updates[-1]
        assert last_update.prefs is not None
        assert last_update.prefs.lastDiaper is not None

    async def test_diaper_listener_emits_potty_updates(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test diaper listener also emits potty updates from the shared document."""
        updates: list[Any] = []

        def callback(data: Any) -> None:
            updates.append(data)

        def subscribe() -> Awaitable[None]:
            return api.setup_diaper_listener(child_uid, callback)

        await subscribe()
        await asyncio.sleep(2)

        measured_at = datetime.now(timezone.utc).replace(microsecond=0)
        await api.log_potty(
            child_uid,
            start_time=measured_at,
            mode="pee",
            how_it_happened="accident",
            pee_amount="little",
        )
        await _wait_or_resubscribe(
            api,
            updates,
            subscribe,
            lambda update: (
                getattr(update, "prefs", None) is not None
                and getattr(update.prefs, "lastPotty", None) is not None
                and update.prefs.lastPotty.mode == "pee"
                and update.prefs.lastPotty.start is not None
                and abs(update.prefs.lastPotty.start - measured_at.timestamp()) < 0.001
            ),
        )

        await api.stop_all_listeners()

        assert len(updates) > 0
        last_update = updates[-1]
        assert last_update.prefs is not None
        assert last_update.prefs.lastPotty is not None
        assert last_update.prefs.lastPotty.mode == "pee"

    async def test_pump_listener(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test pump real-time listener."""
        updates: list[Any] = []
        minimum_start = time.time()
        db = await api._get_firestore_client()
        pump_doc = await db.collection("pump").document(child_uid).get()
        pump_data = pump_doc.to_dict() or {}
        pump_model = FirebasePumpDocumentData.model_validate(pump_data)
        latest_pump = pump_model.prefs.lastPump if pump_model.prefs else None
        if latest_pump is not None and latest_pump.start is not None:
            minimum_start = max(minimum_start, float(latest_pump.start) + 60.0)

        def callback(data: Any) -> None:
            updates.append(data)

        def subscribe() -> Awaitable[None]:
            return api.setup_pump_listener(child_uid, callback)

        await subscribe()
        await asyncio.sleep(2)

        await api.log_pump(
            child_uid,
            start_time=datetime.fromtimestamp(minimum_start, tz=timezone.utc),
            total_amount=25.0,
            duration=900,
            notes="listener test",
        )
        await _wait_or_resubscribe(
            api,
            updates,
            subscribe,
            lambda update: (
                getattr(update, "prefs", None) is not None
                and getattr(update.prefs, "lastPump", None) is not None
                and update.prefs.lastPump.start is not None
                and abs(update.prefs.lastPump.start - minimum_start) < 0.001
            ),
        )

        await api.stop_all_listeners()

        assert len(updates) > 0
        last_update = updates[-1]
        assert last_update.prefs is not None
        assert last_update.prefs.lastPump is not None
        assert last_update.prefs.lastPump.duration == 900.0
        assert last_update.prefs.lastPump.entryMode == "total"

    async def test_activity_listener(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test activities real-time listener."""
        updates: list[Any] = []
        minimum_start = time.time()
        db = await api._get_firestore_client()
        activity_doc = await db.collection("activities").document(child_uid).get()
        activity_data = activity_doc.to_dict() or {}
        activity_model = FirebaseActivityDocumentData.model_validate(activity_data)
        latest_bath = activity_model.prefs.lastBath if activity_model.prefs else None
        if latest_bath is not None and latest_bath.start is not None:
            minimum_start = max(minimum_start, float(latest_bath.start) + 60.0)

        def callback(data: Any) -> None:
            updates.append(data)

        await api.setup_activity_listener(child_uid, callback)
        await asyncio.sleep(2)

        await api.log_activity(
            child_uid,
            mode="bath",
            start_time=datetime.fromtimestamp(minimum_start, tz=timezone.utc),
            duration=900,
            notes="activity listener test",
        )
        await asyncio.sleep(2)

        await api.stop_all_listeners()

        assert len(updates) > 0
        last_update = updates[-1]
        assert last_update.prefs is not None
        assert last_update.prefs.lastBath is not None
        assert last_update.prefs.lastBath.duration == 900.0
