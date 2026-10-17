"""Offline regressions for correlating the imperial growth listener acceptance."""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from unittest.mock import AsyncMock

import pytest

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import FirebaseHealthDocumentData, FirebaseHealthPrefs, FirebaseLastGrowthData
from tests import test_listeners as listener_scenarios


def _health(start: float, *, imperial: bool = False) -> FirebaseHealthDocumentData:
    return FirebaseHealthDocumentData(
        prefs=FirebaseHealthPrefs(
            lastGrowthEntry=FirebaseLastGrowthData(
                _id="synthetic-growth-entry",
                type="health",
                mode="growth",
                start=start,
                offset=0,
                weight=11.5 if imperial else 5.5,
                weightUnits="lbs.oz" if imperial else "kg",
                head=13.8 if imperial else None,
                headUnits="hin" if imperial else None,
            )
        )
    )


@dataclass
class ListenerHarness:
    api: HuckleberryAPI
    write: AsyncMock
    stop: AsyncMock
    timers: list[asyncio.TimerHandle]


def _harness(
    websession,
    monkeypatch,
    *,
    scenario: Literal["interleaved", "delayed", "wrong_values", "no_match", "write_error"] = "interleaved",
    send_baseline: bool = True,
    future_baseline: bool = False,
) -> ListenerHarness:
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    callback: Callable[[FirebaseHealthDocumentData], None] | None = None
    timers: list[asyncio.TimerHandle] = []

    async def subscribe(_child_uid, receive: Callable[[FirebaseHealthDocumentData], None]) -> None:
        nonlocal callback
        callback = receive
        if send_baseline:
            baseline = _health(time.time() + 86400 if future_baseline else time.time() - 60)
            # Match Firestore's worker-thread delivery boundary without contacting it.
            await asyncio.to_thread(receive, baseline)

    async def write(_child_uid, *, start_time, **_measurements) -> None:
        assert callback is not None
        if scenario == "write_error":
            raise RuntimeError("synthetic write failure")
        start = start_time.timestamp()
        if scenario == "no_match":
            callback(_health(start - 1))
        elif scenario == "wrong_values":
            callback(_health(start))
        elif scenario == "delayed":
            timers.append(asyncio.get_running_loop().call_later(0.01, callback, _health(start, imperial=True)))
        else:
            callback(_health(start - 1))
            callback(_health(start, imperial=True))
            callback(_health(start + 1))  # Last callback need not be this test's event.

    write_mock = AsyncMock(side_effect=write)
    stop_mock = AsyncMock()
    monkeypatch.setattr(api, "setup_health_listener", AsyncMock(side_effect=subscribe))
    monkeypatch.setattr(api, "log_growth", write_mock)
    monkeypatch.setattr(api, "stop_all_listeners", stop_mock)
    return ListenerHarness(api, write_mock, stop_mock, timers)


@pytest.mark.parametrize("scenario", ["interleaved", "delayed"])
async def test_imperial_listener_waits_for_its_event(websession, monkeypatch, scenario) -> None:
    """Initial, delayed and unrelated later callbacks cannot change the target."""
    h = _harness(websession, monkeypatch, scenario=scenario)
    try:
        await listener_scenarios.TestRealtimeListeners().test_health_listener_imperial_growth(h.api, "synthetic-child")
    finally:
        for timer in h.timers:
            timer.cancel()
    h.write.assert_awaited_once()
    h.stop.assert_awaited_once()


async def test_imperial_listener_checks_correlated_values(websession, monkeypatch) -> None:
    """A correct event timestamp with wrong values/units is still a failure."""
    h = _harness(websession, monkeypatch, scenario="wrong_values")
    with pytest.raises(AssertionError):
        await listener_scenarios.TestRealtimeListeners().test_health_listener_imperial_growth(h.api, "synthetic-child")
    h.write.assert_awaited_once()
    h.stop.assert_awaited_once()


async def test_imperial_listener_missing_event_times_out_and_stops(websession, monkeypatch) -> None:
    h = _harness(websession, monkeypatch, scenario="no_match")
    monkeypatch.setattr(listener_scenarios, "_HEALTH_LISTENER_TIMEOUT_SECONDS", 0.05)
    with pytest.raises(TimeoutError):
        await listener_scenarios.TestRealtimeListeners().test_health_listener_imperial_growth(h.api, "synthetic-child")
    h.write.assert_awaited_once()
    h.stop.assert_awaited_once()


async def test_imperial_listener_waits_for_baseline_before_writing(websession, monkeypatch) -> None:
    h = _harness(websession, monkeypatch, send_baseline=False)
    monkeypatch.setattr(listener_scenarios, "_HEALTH_LISTENER_TIMEOUT_SECONDS", 0.05)
    with pytest.raises(TimeoutError):
        await listener_scenarios.TestRealtimeListeners().test_health_listener_imperial_growth(h.api, "synthetic-child")
    h.write.assert_not_awaited()
    h.stop.assert_awaited_once()


async def test_imperial_listener_newer_baseline_does_not_write(websession, monkeypatch) -> None:
    h = _harness(websession, monkeypatch, future_baseline=True)
    with pytest.raises(AssertionError, match="future growth"):
        await listener_scenarios.TestRealtimeListeners().test_health_listener_imperial_growth(h.api, "synthetic-child")
    h.write.assert_not_awaited()
    h.stop.assert_awaited_once()


async def test_imperial_listener_write_failure_stops_without_retry(websession, monkeypatch) -> None:
    h = _harness(websession, monkeypatch, scenario="write_error")
    with pytest.raises(RuntimeError, match="synthetic write failure"):
        await listener_scenarios.TestRealtimeListeners().test_health_listener_imperial_growth(h.api, "synthetic-child")
    h.write.assert_awaited_once()
    h.stop.assert_awaited_once()
