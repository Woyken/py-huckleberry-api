"""Unit tests for activity timer writes."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import JsonMap


def _mock_api(websession, monkeypatch, payload: JsonMap):
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = payload

    history_document = MagicMock()
    history_document.set = AsyncMock()
    history_collection = MagicMock()
    history_collection.document.return_value = history_document

    document = MagicMock()
    document.get = AsyncMock(return_value=snapshot)
    document.set = AsyncMock()
    document.update = AsyncMock()
    document.collection.return_value = history_collection

    client = MagicMock()
    client.collection.return_value.document.return_value = document

    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=client))
    return api, document, history_document


async def test_start_activity_reuses_shared_uuid_and_writes_app_shape(websession, monkeypatch) -> None:
    api, document, _ = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "bath": {
                    "active": False,
                    "startTime": 1_789_567_659_188.0,
                    "uuid": "dca86f2ba764cf06",
                }
            }
        },
    )
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_567_721.255)

    await api.start_activity("child", "storyTime")

    document.set.assert_awaited_once_with(
        {
            "timer": {
                "storyTime": {
                    "active": True,
                    "paused": False,
                    "timestamp": {"seconds": 1_789_567_721.255},
                    "local_timestamp": 1_789_567_721.255,
                    "startTime": 1_789_567_721_255.0,
                    "duration": 0,
                    "notes": "",
                    "uuid": "dca86f2ba764cf06",
                }
            }
        },
        merge=True,
    )


async def test_pause_activity_rounds_duration_and_sets_aligned_end_time(websession, monkeypatch) -> None:
    api, document, _ = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "screenTime": {
                    "active": True,
                    "paused": False,
                    "startTime": 1_789_567_842_864.0,
                    "duration": 0,
                    "notes": "",
                    "uuid": "dca86f2ba764cf06",
                }
            }
        },
    )
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_567_864.575)

    await api.pause_activity("child", "screenTime")

    document.set.assert_awaited_once_with(
        {
            "timer": {
                "screenTime": {
                    "active": True,
                    "paused": True,
                    "timestamp": {"seconds": 1_789_567_864.575},
                    "local_timestamp": 1_789_567_864.575,
                    "duration": 22,
                    "endTime": 1_789_567_864_864.0,
                }
            }
        },
        merge=True,
    )


async def test_resume_activity_includes_paused_wall_clock_time_and_keeps_end_time(websession, monkeypatch) -> None:
    api, document, _ = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "screenTime": {
                    "active": True,
                    "paused": True,
                    "startTime": 1_789_567_842_864.0,
                    "endTime": 1_789_567_864_864.0,
                    "duration": 22,
                    "notes": "",
                    "uuid": "dca86f2ba764cf06",
                }
            }
        },
    )
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_567_909.825)

    await api.resume_activity("child", "screenTime")

    document.set.assert_awaited_once_with(
        {
            "timer": {
                "screenTime": {
                    "active": True,
                    "paused": False,
                    "timestamp": {"seconds": 1_789_567_909.825},
                    "local_timestamp": 1_789_567_909.825,
                    "duration": 67,
                }
            }
        },
        merge=True,
    )


async def test_cancel_activity_resets_without_writing_history(websession, monkeypatch) -> None:
    api, document, history_document = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "storyTime": {
                    "active": True,
                    "paused": False,
                    "startTime": 1_789_567_721_254.0,
                    "duration": 0,
                    "notes": "",
                    "uuid": "dca86f2ba764cf06",
                }
            }
        },
    )
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_567_774.064)

    await api.cancel_activity("child", "storyTime")

    document.set.assert_awaited_once_with(
        {
            "timer": {
                "storyTime": {
                    "active": False,
                    "paused": False,
                    "timestamp": {"seconds": 1_789_567_774.064},
                    "local_timestamp": 1_789_567_774.064,
                    "startTime": 1_789_567_774_064.0,
                    "endTime": 1_789_567_774_254.0,
                    "duration": 53,
                    "notes": "",
                    "uuid": "dca86f2ba764cf06",
                }
            }
        },
        merge=True,
    )
    history_document.set.assert_not_awaited()


async def test_complete_paused_activity_uses_edits_and_frozen_timer_start(websession, monkeypatch) -> None:
    api, document, _ = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "skinToSkin": {
                    "active": True,
                    "paused": True,
                    "startTime": 1_789_568_022_000.0,
                    "endTime": 1_789_568_137_000.0,
                    "duration": 115,
                    "notes": "",
                    "uuid": "dca86f2ba764cf06",
                }
            }
        },
    )
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_568_178.097)
    log_activity = AsyncMock()
    monkeypatch.setattr(api, "_log_activity", log_activity)
    edited_start = datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc)

    await api.complete_activity(
        "child",
        "skinToSkin",
        start_time=edited_start,
        duration=120,
        notes="issue35-live-edited",
    )

    log_activity.assert_awaited_once_with(
        "child",
        mode="skinToSkin",
        start_time=edited_start,
        duration=120.0,
        notes="issue35-live-edited",
        update_timer=False,
    )
    document.set.assert_awaited_once_with(
        {
            "timer": {
                "skinToSkin": {
                    "active": False,
                    "paused": False,
                    "timestamp": {"seconds": 1_789_568_178.097},
                    "local_timestamp": 1_789_568_178.097,
                    "startTime": 1_789_568_178_097.0,
                    "endTime": 1_789_568_142_000.0,
                    "duration": 120.0,
                    "notes": "",
                    "uuid": "dca86f2ba764cf06",
                }
            }
        },
        merge=True,
    )


@pytest.mark.parametrize("paused", [False, True])
async def test_switch_activity_moves_running_or_paused_session(websession, monkeypatch, paused: bool) -> None:
    source = {
        "active": True,
        "paused": paused,
        "startTime": 1_789_568_402_470.0,
        "duration": 128 if paused else 0,
        "notes": "",
        "uuid": "dca86f2ba764cf06",
    }
    if paused:
        source["endTime"] = 1_789_568_530_470.0
    api, document, _ = _mock_api(websession, monkeypatch, {"timer": {"brushTeeth": source}})
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_568_561.578)

    await api.switch_activity("child", "tummyTime")

    payload = document.set.await_args.args[0]["timer"]
    assert payload["brushTeeth"] == {
        "active": False,
        "paused": False,
        "timestamp": {"seconds": 1_789_568_561.578},
        "local_timestamp": 1_789_568_561.578,
        "startTime": 1_789_568_561_578.0,
    }
    assert payload["tummyTime"] == {
        "active": True,
        "paused": paused,
        "timestamp": {"seconds": 1_789_568_561.578},
        "local_timestamp": 1_789_568_561.578,
        "startTime": 1_789_568_402_470.0,
        "duration": 128.0 if paused else 159.0,
        "notes": "",
        "uuid": "dca86f2ba764cf06",
        **({"endTime": 1_789_568_530_470.0} if paused else {}),
    }
    assert document.set.await_args.kwargs == {"merge": True}


async def test_manual_activity_save_creates_inactive_timer_entry(websession, monkeypatch) -> None:
    api, document, _ = _mock_api(
        websession,
        monkeypatch,
        {
            "timer": {
                "bath": {
                    "active": False,
                    "uuid": "dca86f2ba764cf06",
                }
            }
        },
    )
    monkeypatch.setattr(api, "_get_timezone_offset_minutes", AsyncMock(return_value=-180))
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_568_304.257)

    await api.log_activity(
        "child",
        mode="indoorPlay",
        start_time=datetime(2026, 9, 16, 15, 30, tzinfo=timezone.utc),
        notes="issue35-live-manual",
    )

    document.set.assert_awaited_once_with(
        {
            "timer": {
                "indoorPlay": {
                    "active": False,
                    "paused": False,
                    "timestamp": {"seconds": 1_789_568_304.257},
                    "local_timestamp": 1_789_568_304.257,
                    "startTime": 1_789_568_304_257.0,
                    "duration": 0,
                    "notes": "",
                    "uuid": "dca86f2ba764cf06",
                }
            }
        },
        merge=True,
    )


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf")])
async def test_complete_activity_rejects_invalid_duration_before_firebase(
    websession, monkeypatch, duration: float
) -> None:
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    get_client = AsyncMock()
    monkeypatch.setattr(api, "_get_firestore_client", get_client)

    with pytest.raises(ValueError, match="non-negative finite"):
        await api.complete_activity("child", "bath", duration=duration)

    get_client.assert_not_awaited()
