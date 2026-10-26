"""Regression tests for legacy feed timer timestamp units."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from huckleberry_api import HuckleberryAPI


def _mock_nursing_api(
    websession,
    monkeypatch,
    timer: dict[str, object],
) -> tuple[HuckleberryAPI, MagicMock, MagicMock]:
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"timer": timer}

    interval_document = MagicMock()
    interval_document.set = AsyncMock()
    intervals = MagicMock()
    intervals.document.return_value = interval_document

    feed_document = MagicMock()
    feed_document.get = AsyncMock(return_value=snapshot)
    feed_document.collection.return_value = intervals
    feed_document.update = AsyncMock()

    client = MagicMock()
    client.collection.return_value.document.return_value = feed_document

    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=client))
    monkeypatch.setattr(api, "_get_timezone_offset_minutes", AsyncMock(return_value=0))
    return api, feed_document, interval_document


async def test_complete_nursing_normalizes_millisecond_feed_start_time(websession, monkeypatch) -> None:
    """Completing an app-started session must write a seconds-based history row."""
    now = time.time()
    feed_start_seconds = now - 600
    api, feed_document, interval_document = _mock_nursing_api(
        websession,
        monkeypatch,
        {
            "active": True,
            "paused": False,
            "feedStartTime": feed_start_seconds * 1000,
            "timerStartTime": now - 300,
            "uuid": "nursing-session",
            "leftDuration": 0.0,
            "rightDuration": 0.0,
            "lastSide": "right",
            "activeSide": "right",
        },
    )

    await api.complete_nursing("child")

    interval_payload = interval_document.set.await_args.args[0]
    assert interval_payload["start"] == pytest.approx(feed_start_seconds, abs=1)

    update_payload = feed_document.update.await_args.args[0]
    assert update_payload["prefs.lastNursing"]["start"] == pytest.approx(feed_start_seconds, abs=1)
    assert update_payload["prefs.lastSide"]["start"] == pytest.approx(feed_start_seconds, abs=1)


async def test_complete_nursing_keeps_second_feed_start_time(websession, monkeypatch) -> None:
    """Normal seconds-based sessions are unchanged."""
    now = time.time()
    feed_start_seconds = now - 600
    api, feed_document, interval_document = _mock_nursing_api(
        websession,
        monkeypatch,
        {
            "active": True,
            "paused": False,
            "feedStartTime": feed_start_seconds,
            "timerStartTime": now - 300,
            "uuid": "nursing-session",
            "leftDuration": 0.0,
            "rightDuration": 0.0,
            "lastSide": "right",
            "activeSide": "right",
        },
    )

    await api.complete_nursing("child")

    interval_payload = interval_document.set.await_args.args[0]
    assert interval_payload["start"] == pytest.approx(feed_start_seconds, abs=1)

    update_payload = feed_document.update.await_args.args[0]
    assert update_payload["prefs.lastNursing"]["start"] == pytest.approx(feed_start_seconds, abs=1)
