"""Unit tests for the local SweetSpot next-nap port."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import cast
import pytest

from aiohttp import ClientSession

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import (
    FirebaseChildDocument,
    FirebaseSettingsPlusDocument,
    FirebaseSleepIntervalData,
)
from huckleberry_api.sweetspot import (
    DaySleepStats,
    age_months,
    build_sleep_minutes,
    calculate_next_nap,
    calculate_wake_windows,
    child_morning_cutoff,
    child_night_start,
    default_wake_windows,
    sleep_stats_from_minutes,
    user_nap_counts,
    user_wake_windows,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 26, 13, 30, tzinfo=UTC)


def _minutes(hour: int, minute: int = 0) -> int:
    return hour * 60 + minute


def _settings() -> FirebaseSettingsPlusDocument:
    return FirebaseSettingsPlusDocument.model_validate(
        {
            "napWindow": [
                {
                    "lowerAge": 6,
                    "upperAge": 6,
                    "2": [2.5, 2.75, 3.0],
                    "3": [1.75, 2.0, 2.25, 2.5],
                    "extraNaps": {"2": [3.0, 2.5], "3": [2.5]},
                    "limits": {"3": [1.0, 4.0]},
                },
                {
                    "lowerAge": 7,
                    "upperAge": 7,
                    "2": [2.75, 3.0, 3.25],
                    "3": [2.0, 2.25, 2.5, 2.75],
                    "extraNaps": {"2": [3.0, 2.5], "3": [2.5]},
                    "limits": {"3": [1.0, 4.0]},
                },
            ]
        }
    )


def _child(**overrides: object) -> FirebaseChildDocument:
    payload: dict[str, object] = {
        "naps": "3",
        "nightStart": 6,
        "morningCutoff": 8,
        "birthdate": "2026-04-26",
        "pre": 0,
    }
    payload.update(overrides)
    return FirebaseChildDocument.model_validate(payload)


def _sleep_minutes(*spans: tuple[int, int]) -> set[int]:
    minutes: set[int] = set()
    for start, end in spans:
        minutes.update(range(start, end))
    return minutes


def _today_stats() -> DaySleepStats:
    return sleep_stats_from_minutes(
        _sleep_minutes(
            (0, _minutes(7)),
            (_minutes(9), _minutes(10)),
            (_minutes(12), _minutes(13)),
        ),
        morning_cutoff=8,
        night_start=6,
    )


def _interval(start: datetime, duration: int) -> FirebaseSleepIntervalData:
    return FirebaseSleepIntervalData.model_validate({"start": start.timestamp(), "duration": duration, "offset": 0})


def test_user_nap_counts_mapping() -> None:
    assert user_nap_counts("3") == [3]
    assert user_nap_counts("2-3") == [2, 3]
    assert user_nap_counts(">=5") == [5]
    assert user_nap_counts(None) == []


def test_age_months_adjusts_for_prematurity() -> None:
    assert age_months(_child(birthdate="2026-04-26"), date(2026, 10, 26)) == 6
    assert age_months(_child(birthdate="2026-04-26", pre=4), date(2026, 10, 26)) == 5
    assert age_months(_child(birthdate=None), date(2026, 10, 26)) == 0


def test_age_months_handles_month_ends() -> None:
    child = _child(birthdate="2026-01-31", pre=0)

    assert age_months(child, date(2026, 2, 28)) == 1
    assert age_months(child, date(2026, 2, 27)) == 0


def test_sleep_stats_marks_evening_sleep_as_night_start() -> None:
    stats = sleep_stats_from_minutes(
        _sleep_minutes(
            (0, _minutes(7)),
            (_minutes(9), _minutes(10)),
            (_minutes(19), _minutes(24)),
        ),
        morning_cutoff=8,
        night_start=6,
    )

    assert stats.night_start == _minutes(19)
    assert stats.nap_type == 1
    assert stats.wake_windows == [121, 541]


def test_sleep_stats_from_minutes() -> None:
    stats = _today_stats()

    assert stats.morning_wake == _minutes(6, 59)
    assert stats.last_sleep == _minutes(12, 59)
    assert [(nap.start, nap.end) for nap in stats.naps] == [
        (_minutes(9), _minutes(9, 59)),
        (_minutes(12), _minutes(12, 59)),
    ]
    assert stats.nap_type == 2


def test_sleep_stats_handles_sleep_ending_before_minute_30() -> None:
    """A night ending before minute 30 still yields wake stats instead of raising."""
    stats = sleep_stats_from_minutes(
        _sleep_minutes((0, 15)),
        morning_cutoff=8,
        night_start=6,
    )

    assert stats.morning_wake == 14
    assert stats.last_sleep == 14
    assert stats.naps == []


def test_build_sleep_minutes_clips_to_day() -> None:
    day_start = datetime(2026, 10, 26, tzinfo=UTC)
    day_end = datetime(2026, 10, 27, tzinfo=UTC)
    intervals = [
        _interval(datetime(2026, 10, 25, 23, 30, tzinfo=UTC), 3600),
        _interval(datetime(2026, 10, 26, 1, 0, tzinfo=UTC), 1800),
    ]

    minutes = build_sleep_minutes(intervals, day_start, day_end)

    assert 0 in minutes
    assert 29 in minutes
    assert _minutes(1) in minutes
    assert _minutes(1, 30) not in minutes


def test_default_wake_windows_uses_age_band() -> None:
    config = default_wake_windows(_settings(), 3, 6)

    assert config is not None
    assert config.gaps == [1.75, 2.0, 2.25, 2.5]
    assert config.extra_naps == {"2": [3.0, 2.5], "3": [2.5]}
    assert config.limits == {"3": [1.0, 4.0]}


def test_next_nap_default() -> None:
    child = _child()

    result = calculate_next_nap(
        child,
        _settings(),
        today_stats=_today_stats(),
        nap_count=3,
        age=6,
        now=NOW,
        timezone=UTC,
    )

    assert result is not None
    assert result.wake_window == 2.25
    assert result.nap_num == 2
    assert result.source == "default"
    assert result.next_nap == _minutes(12, 59) * 60 + 2.25 * 3600


def test_next_nap_uses_extra_naps_when_day_has_extra_naps() -> None:
    sleep = _sleep_minutes(
        (0, _minutes(7)),
        (_minutes(9), _minutes(10)),
        (_minutes(12), _minutes(13)),
        (_minutes(15), _minutes(16)),
    )
    stats = sleep_stats_from_minutes(sleep, morning_cutoff=8, night_start=6)
    assert len(stats.naps) == 3

    result = calculate_next_nap(
        _child(),
        _settings(),
        today_stats=stats,
        nap_count=2,
        age=6,
        now=NOW,
        timezone=UTC,
    )

    assert result is not None
    assert result.wake_window == 2.5
    assert result.nap_num == 2


def test_short_last_nap_shortens_wake_window() -> None:
    stats = sleep_stats_from_minutes(
        _sleep_minutes(
            (0, _minutes(7)),
            (_minutes(9), _minutes(10)),
            (_minutes(12), _minutes(12, 20)),
        ),
        morning_cutoff=8,
        night_start=6,
    )

    result = calculate_next_nap(
        _child(),
        _settings(),
        today_stats=stats,
        nap_count=3,
        age=6,
        now=NOW,
        timezone=UTC,
    )

    assert result is not None
    assert result.wake_window == pytest.approx(2.25 * 0.8)


def test_custom_nap_window_and_relative_adjustment() -> None:
    child = _child(
        customNapWindow={"lowerAge": 6, "upperAge": 6, "3": [1.5, 1.75, 2.0, 2.25]},
        sweetspotAdjust={
            "custom": True,
            "windows": {"3": {"2": {"relative": 30}}},
        },
    )

    config = user_wake_windows(
        child,
        _settings(),
        nap_count=3,
        age=6,
        now=NOW,
    )

    assert config is not None
    assert config.source == "user_adjust-relative-default"
    assert config.gaps == [1.5, 1.75, 2.5, 2.25]


def test_absolute_adjustment() -> None:
    child = _child(
        sweetspotAdjust={
            "custom": True,
            "windows": {"3": {"0": {"manual": True, "absolute": 1.25}}},
        }
    )

    config = user_wake_windows(
        child,
        _settings(),
        nap_count=3,
        age=6,
        now=NOW,
    )

    assert config is not None
    assert config.source == "user_adjust-absolute"
    assert config.gaps[0] == 1.25


def test_adjustment_beyond_gap_count_is_ignored() -> None:
    child = _child(
        customNapWindow={"lowerAge": 6, "upperAge": 6, "3": [1.5, 1.75]},
        sweetspotAdjust={
            "custom": True,
            "windows": {"3": {"3": {"relative": 30}}},
        },
    )

    config = user_wake_windows(
        child,
        _settings(),
        nap_count=3,
        age=6,
        now=NOW,
    )

    assert config is not None
    assert config.gaps == [1.5, 1.75]
    assert config.source == "sc_custom"


def test_custom_nap_window_without_ages_still_predicts() -> None:
    """A partial `customNapWindow` with nap keys but no ages must not break replacement checks."""
    child = _child(customNapWindow={"3": [1.5, 1.75, 2.0, 2.25]})

    result = calculate_next_nap(
        child,
        _settings(),
        today_stats=_today_stats(),
        nap_count=3,
        age=6,
        now=NOW,
        timezone=UTC,
    )

    assert result is not None


def test_displayed_sweetspot_overrides_today() -> None:
    child = _child(
        sweetspot={
            "displayedSweetSpot": {
                "3": {
                    "W3": {
                        "timestamp": NOW.timestamp(),
                        "value": 1.5,
                        "source": "ai_ml",
                    }
                }
            }
        }
    )

    result = calculate_next_nap(
        child,
        _settings(),
        today_stats=_today_stats(),
        nap_count=3,
        age=6,
        now=NOW,
        timezone=UTC,
    )

    assert result is not None
    assert result.wake_window == 1.5
    assert result.source == "ai_ml"


def test_displayed_sweetspot_ignored_when_from_another_day() -> None:
    child = _child(
        sweetspot={
            "displayedSweetSpot": {
                "3": {
                    "W3": {
                        "timestamp": datetime(2026, 10, 25, 13, 30, tzinfo=UTC).timestamp(),
                        "value": 1.5,
                        "source": "ai_ml",
                    }
                }
            }
        }
    )

    result = calculate_next_nap(
        child,
        _settings(),
        today_stats=_today_stats(),
        nap_count=3,
        age=6,
        now=NOW,
        timezone=UTC,
    )

    assert result is not None
    assert result.wake_window == 2.25
    assert result.source == "default"


def test_persisted_transition_wake_windows_are_used() -> None:
    child = _child(
        naps="2-3",
        calculatedWakeWindows={
            "2": {
                "timestamp": NOW.timestamp(),
                "wakeWindow": [3.0, 3.25, 3.5],
                "transition": True,
            }
        },
    )

    config = user_wake_windows(
        child,
        _settings(),
        nap_count=2,
        age=6,
        now=NOW,
    )

    assert config is not None
    assert config.source == "transition_wake_window"
    assert config.gaps == [3.0, 3.25, 3.5]


def test_calculated_wake_windows_detects_two_to_three_transition() -> None:
    history = [DaySleepStats(nap_type=3, wake_windows=[2.0, 2.25, 2.5, 2.75]) for _ in range(30)]

    transition, extracted = calculate_wake_windows(
        6,
        history,
        _settings(),
        nap_counts=[2],
    )

    assert transition is True
    assert 2 in extracted
    assert len(extracted[2]) == 3
    assert 3 in extracted


def test_transition_wake_windows_require_full_bands() -> None:
    """Short 2-nap or 3-nap bands skip the transition instead of indexing past the end."""
    settings = FirebaseSettingsPlusDocument.model_validate(
        {
            "napWindow": [
                {
                    "lowerAge": 6,
                    "upperAge": 6,
                    "2": [2.5],
                    "3": [1.75, 2.0, 2.25, 2.5],
                }
            ]
        }
    )
    history = [DaySleepStats(nap_type=3, wake_windows=[2.0, 2.25, 2.5, 2.75]) for _ in range(30)]

    transition, extracted = calculate_wake_windows(6, history, settings, nap_counts=[2])

    assert transition is True
    assert extracted[2] == []

    config = user_wake_windows(
        _child(naps="2-3"),
        settings,
        nap_count=2,
        age=6,
        now=NOW,
        history=history,
    )

    assert config is not None
    assert config.gaps == [2.5]
    assert config.source == "default"


def test_child_time_settings_fall_back_to_defaults() -> None:
    child = _child(nightStart=99, morningCutoff=0)

    assert child_morning_cutoff(child) == 8
    assert child_night_start(child) == 6


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self) -> dict[str, object]:
        return self._payload


class _FakeSession:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    def get(self, *args: object, **kwargs: object) -> _FakeResponse:
        self.calls += 1
        return _FakeResponse(self.payload)


@pytest.mark.asyncio
async def test_settings_fetch_is_cached() -> None:
    payload = _settings().model_dump(by_alias=True, exclude_none=True)
    session = _FakeSession(payload)
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=cast(ClientSession, session))
    api.id_token = "token"
    api.token_expires_at = datetime.now().timestamp() + 3600

    first = await api._get_sweetspot_settings()
    second = await api._get_sweetspot_settings()

    assert first is second
    assert session.calls == 1


@pytest.mark.asyncio
async def test_get_next_nap_wires_storage_and_sleep_data() -> None:
    session = _FakeSession(_settings().model_dump(by_alias=True, exclude_none=True))
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=cast(ClientSession, session))
    api.id_token = "token"
    api.token_expires_at = datetime.now().timestamp() + 3600
    child = _child()

    async def fake_get_child(child_uid: str) -> FirebaseChildDocument:
        return child

    async def fake_list_sleep_intervals(
        child_uid: str, start_time: datetime, end_time: datetime
    ) -> list[FirebaseSleepIntervalData]:
        return [
            _interval(datetime(2026, 10, 26, 0, 0, tzinfo=UTC), 7 * 3600),
            _interval(datetime(2026, 10, 26, 9, 0, tzinfo=UTC), 3600),
            _interval(datetime(2026, 10, 26, 12, 0, tzinfo=UTC), 3600),
        ]

    api.get_child = fake_get_child  # type: ignore[method-assign]
    api.list_sleep_intervals = fake_list_sleep_intervals  # type: ignore[method-assign]

    result = await api.get_next_nap("child", now=NOW)

    assert result is not None
    assert result.next_nap == datetime(2026, 10, 26, tzinfo=UTC).timestamp() + 15 * 3600 + 10 * 60
    assert result.wake_window == 2.25
    assert result.wake_window_number == 3
    assert result.nap_count == 3
    assert result.source == "default"


def test_missing_settings_band_returns_none() -> None:
    settings = FirebaseSettingsPlusDocument.model_validate({"napWindow": []})

    assert (
        calculate_next_nap(
            _child(),
            settings,
            today_stats=_today_stats(),
            nap_count=3,
            age=6,
            now=NOW,
            timezone=UTC,
        )
        is None
    )
