"""Regression tests for failed Firebase history writes."""

from datetime import datetime, timezone
from typing import Literal, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.api_core.exceptions import GoogleAPICallError

from huckleberry_api import HuckleberryAPI


def _mock_api(
    websession,
    monkeypatch,
    root_payload: dict[str, object],
    write_error: GoogleAPICallError | None = None,
):
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = root_payload

    history_document = MagicMock()
    history_document.set = AsyncMock(side_effect=write_error)
    history_collection = MagicMock()
    history_collection.document.return_value = history_document

    root_document = MagicMock()
    root_document.get = AsyncMock(return_value=snapshot)
    root_document.collection.return_value = history_collection
    root_document.update = AsyncMock()

    client = MagicMock()
    client.collection.return_value.document.return_value = root_document

    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=client))
    monkeypatch.setattr(api, "_get_timezone_offset_minutes", AsyncMock(return_value=0))
    return api, root_document, history_document


async def test_log_growth_propagates_history_write_failure(websession, monkeypatch) -> None:
    api, health_document, history_document = _mock_api(
        websession,
        monkeypatch,
        {},
        GoogleAPICallError("history write failed"),
    )

    with pytest.raises(GoogleAPICallError, match="history write failed"):
        await api.log_growth(
            "child",
            start_time=datetime(2026, 9, 15, tzinfo=timezone.utc),
            weight=5.0,
        )

    health_document.update.assert_not_awaited()
    history_document.set.assert_awaited_once()


async def test_complete_nursing_propagates_history_write_failure(websession, monkeypatch) -> None:
    api, feed_document, history_document = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "active": True,
                "paused": False,
                "feedStartTime": 100.0,
                "timerStartTime": 100.0,
                "uuid": "nursing-session",
                "leftDuration": 0.0,
                "rightDuration": 0.0,
                "lastSide": "left",
                "activeSide": "left",
            }
        },
        GoogleAPICallError("history write failed"),
    )

    with pytest.raises(GoogleAPICallError, match="history write failed"):
        await api.complete_nursing("child")

    feed_document.update.assert_not_awaited()
    history_document.set.assert_awaited_once()


async def test_log_growth_writes_distinct_history_and_latest_payloads(websession, monkeypatch) -> None:
    api, health_document, history_document = _mock_api(websession, monkeypatch, {})
    measured_at = datetime(2026, 9, 15, tzinfo=timezone.utc)

    await api.log_growth("child", start_time=measured_at, weight=5.0, units="metric")

    history_payload = history_document.set.await_args.args[0]
    assert history_payload == {
        "mode": "growth",
        "start": measured_at.timestamp(),
        "lastUpdated": history_payload["lastUpdated"],
        "offset": 0,
        "weight": 5.0,
        "weightUnits": "kg",
    }
    latest_payload = health_document.update.await_args.args[0]["prefs.lastGrowthEntry"]
    assert latest_payload["_id"]
    assert latest_payload["type"] == "health"
    assert latest_payload["isNight"] is False
    assert latest_payload["multientry_key"] is None


async def test_log_growth_rejects_unknown_units_before_accessing_firebase(websession, monkeypatch) -> None:
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    get_client = AsyncMock()
    monkeypatch.setattr(api, "_get_firestore_client", get_client)
    invalid_units = cast(Literal["metric", "imperial"], "invalid")

    with pytest.raises(ValueError, match="units"):
        await api.log_growth(
            "child",
            start_time=datetime(2026, 9, 15, tzinfo=timezone.utc),
            weight=5.0,
            units=invalid_units,
        )

    get_client.assert_not_awaited()
