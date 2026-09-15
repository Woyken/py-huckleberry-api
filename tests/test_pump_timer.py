"""Unit tests for pumping timer writes."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.cloud.firestore import DELETE_FIELD

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import JsonMap


def _mock_api(websession, monkeypatch, payload: JsonMap):
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = payload

    document = MagicMock()
    document.get = AsyncMock(return_value=snapshot)
    document.set = AsyncMock()
    document.update = AsyncMock()

    client = MagicMock()
    client.collection.return_value.document.return_value = document

    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=client))
    return api, document


async def test_start_pump_preserves_uuid_and_writes_minimal_timer_shape(websession, monkeypatch) -> None:
    api, document = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "active": False,
                "startTime": 1_789_483_139_988.0,
                "uuid": "dca86f2ba764cf06",
            }
        },
    )
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_483_269.806)

    await api.start_pump("child")

    document.set.assert_awaited_once_with(
        {
            "timer": {
                "active": True,
                "paused": False,
                "timestamp": {"seconds": 1_789_483_269.806},
                "local_timestamp": 1_789_483_269.806,
                "startTime": 1_789_483_269_806.0,
                "uuid": "dca86f2ba764cf06",
            }
        },
        merge=True,
    )


async def test_pause_and_resume_pump_manage_end_time(websession, monkeypatch) -> None:
    api, document = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "active": True,
                "paused": False,
                "startTime": 1_789_482_890_482.0,
                "entryMode": "total",
                "units": "ml",
                "uuid": "dca86f2ba764cf06",
            }
        },
    )
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_482_916.021)

    await api.pause_pump("child")

    document.update.assert_awaited_once_with(
        {
            "timer.active": True,
            "timer.paused": True,
            "timer.endTime": 1_789_482_916_021.0,
            "timer.timestamp": {"seconds": 1_789_482_916.021},
            "timer.local_timestamp": 1_789_482_916.021,
        }
    )

    document.get.return_value.to_dict.return_value["timer"]["paused"] = True
    document.update.reset_mock()
    await api.resume_pump("child")

    update = document.update.await_args.args[0]
    assert update == {
        "timer.active": True,
        "timer.paused": False,
        "timer.endTime": DELETE_FIELD,
        "timer.timestamp": {"seconds": 1_789_482_916.021},
        "timer.local_timestamp": 1_789_482_916.021,
    }


async def test_cancel_pump_restores_observed_inactive_shape(websession, monkeypatch) -> None:
    api, document = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "active": True,
                "paused": True,
                "startTime": 1_789_482_890_482.0,
                "endTime": 1_789_482_916_021.0,
                "entryMode": "total",
                "units": "ml",
                "uuid": "dca86f2ba764cf06",
            }
        },
    )
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_483_139.988)

    await api.cancel_pump("child")

    document.update.assert_awaited_once_with(
        {
            "timer": {
                "active": False,
                "timestamp": {"seconds": 1_789_483_139.988},
                "local_timestamp": 1_789_483_139.988,
                "startTime": 1_789_483_139_988.0,
                "uuid": "dca86f2ba764cf06",
            }
        }
    )


async def test_complete_paused_pump_uses_end_time_and_resets_after_write(websession, monkeypatch) -> None:
    api, document = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "active": True,
                "paused": True,
                "startTime": 1_789_482_890_482.0,
                "endTime": 1_789_482_916_021.0,
                "entryMode": "total",
                "units": "ml",
                "uuid": "dca86f2ba764cf06",
            }
        },
    )
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_483_098.562)
    log_pump = AsyncMock()
    monkeypatch.setattr(api, "_log_pump", log_pump)

    await api.complete_pump("child", total_amount=42, notes="issue34-total-live")

    log_pump.assert_awaited_once_with(
        "child",
        start_time=datetime.fromtimestamp(1_789_482_890.482, tz=timezone.utc),
        duration=25.539,
        left_amount=None,
        right_amount=None,
        total_amount=42,
        units="ml",
        notes="issue34-total-live",
        update_pref_timestamps=False,
    )
    document.update.assert_awaited_once_with(
        {
            "timer": {
                "active": False,
                "timestamp": {"seconds": 1_789_483_098.562},
                "local_timestamp": 1_789_483_098.562,
                "startTime": 1_789_483_098_562.0,
                "uuid": "dca86f2ba764cf06",
            }
        }
    )


async def test_complete_resumed_pump_includes_elapsed_wall_clock_time(websession, monkeypatch) -> None:
    api, _ = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "active": True,
                "paused": False,
                "startTime": 1_789_482_890_482.0,
                "entryMode": "leftright",
                "units": "oz",
                "uuid": "dca86f2ba764cf06",
            }
        },
    )
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_483_098.562)
    log_pump = AsyncMock()
    monkeypatch.setattr(api, "_log_pump", log_pump)

    await api.complete_pump("child", left_amount=2.5, right_amount=3.75)

    assert log_pump.await_args is not None
    call = log_pump.await_args.kwargs
    assert call["duration"] == 208.08
    assert call["left_amount"] == 2.5
    assert call["right_amount"] == 3.75
    assert call["total_amount"] is None
    assert call["units"] == "oz"


async def test_complete_pump_validates_amounts_before_firebase(websession, monkeypatch) -> None:
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    get_client = AsyncMock()
    monkeypatch.setattr(api, "_get_firestore_client", get_client)

    with pytest.raises(ValueError, match="non-negative finite"):
        await api.complete_pump("child", left_amount=1, right_amount=float("inf"))

    get_client.assert_not_awaited()


@pytest.mark.parametrize("amount", [-1, float("nan"), float("inf")])
async def test_log_pump_rejects_invalid_amounts_before_firebase(websession, monkeypatch, amount: float) -> None:
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    get_client = AsyncMock()
    monkeypatch.setattr(api, "_get_firestore_client", get_client)

    with pytest.raises(ValueError, match="non-negative finite"):
        await api.log_pump(
            "child",
            start_time=datetime(2026, 9, 15, tzinfo=timezone.utc),
            total_amount=amount,
        )

    get_client.assert_not_awaited()
