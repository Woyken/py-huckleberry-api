"""Read-only port of the Huckleberry app's local SweetSpot next-nap calculation.

Ported from app version 0.9.313 (`getNextNap`, `getUserWakeWindows`,
`calculatedWakeWindows`, and `getSleepStats`). The app persists its own result;
this module only computes a prediction for callers such as Home Assistant.

The AI/RL model calls are not reproducible from a Python client, so this port
uses the wake-window baseline and reuses today's `displayedSweetSpot` slot when
the app has already written one. The app also gates replacement on remote
experiment flags (`sweetspot_personalization_*`) and a subscription check that
are not stored on the child document; this port applies `displayedSweetSpot`
whenever the child has no custom SweetSpot/sleep-consultant windows and applies
stored `sweetspotAdjust` windows and `calculatedWakeWindows` transitions without
those gates. Unlike the app it never writes `calculatedWakeWindows` back.
Sleep intervals are read by absolute timestamp, so a logged timezone `offset` is
interpreted in the child's current timezone.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone as dt_timezone, tzinfo
from typing import Any, Iterable, Sequence

from .firebase_types import (
    FirebaseChildCustomNapWindow,
    FirebaseChildDocument,
    FirebaseSettingsNapWindow,
    FirebaseSettingsPlusDocument,
    FirebaseSleepIntervalData,
    Number,
)

SOURCE_DEFAULT = "default"
SOURCE_SC_CUSTOM = "sc_custom"
SOURCE_USER_ADJUST_RELATIVE_DEFAULT = "user_adjust-relative-default"
SOURCE_USER_ADJUST_ABSOLUTE = "user_adjust-absolute"
SOURCE_TRANSITION = "transition_wake_window"

_NAP_COUNTS: dict[str, tuple[int, int | None]] = {
    "0": (0, None),
    "0-1": (0, 1),
    "1": (1, None),
    "1-2": (1, 2),
    "2": (2, None),
    "2-3": (2, 3),
    "3": (3, None),
    "3-4": (3, 4),
    "4": (4, None),
    "4-5": (4, 5),
    "5": (5, None),
    ">=5": (5, None),
}

_LAST_MINUTE = 24 * 60 - 1


@dataclass
class SleepSpan:
    """Sleep block in minutes from the child's local midnight."""

    start: int
    end: int | None = None


@dataclass
class DaySleepStats:
    """Subset of the app's `getSleepStats` result needed by the prediction path."""

    morning_wake: int | None = None
    naps: list[SleepSpan] = field(default_factory=list)
    linked_sleep: list[SleepSpan] = field(default_factory=list)
    linked_naps: list[SleepSpan] = field(default_factory=list)
    last_sleep: int | None = None
    night_start: int | None = None
    nap_type: int | None = None
    wake_windows: list[float] = field(default_factory=list)


@dataclass
class WakeWindowConfig:
    """Wake windows selected for one nap schedule."""

    gaps: list[float]
    extra_naps: dict[str, list[float]] | None = None
    limits: dict[str, list[float]] | None = None
    lower_age: float | None = None
    upper_age: float | None = None
    original_values: list[float] = field(default_factory=list)
    source: str = SOURCE_DEFAULT


@dataclass(frozen=True)
class NextNapResult:
    """Computed next nap, in seconds from the child's local midnight."""

    next_nap: float
    wake_window: float
    source: str
    nap_num: int
    nap_count: int


def user_nap_counts(naps: str | None) -> list[int]:
    """Return the app's primary and optional secondary nap counts."""
    counts = _NAP_COUNTS.get(naps or "")
    if counts is None:
        return []
    primary, secondary = counts
    return [count for count in (primary, secondary) if count is not None]


def age_months(child: FirebaseChildDocument, today: date) -> int:
    """Return the app's integer age in months, adjusted for weeks premature.

    Mirrors `getAgeMonthsInteger`, which floors moment's fractional month diff.
    """
    birthdate = _parse_birthdate(child.birthdate)
    if birthdate is None:
        return 0

    pre_weeks = child.pre if isinstance(child.pre, (int, float)) and child.pre > 0 else 0
    adjusted = birthdate + timedelta(weeks=pre_weeks)
    months = round(100 * _month_diff(adjusted, today)) / 100
    return max(math.floor(months), 0)


def _month_diff(earlier: date, later: date) -> float:
    """Port of moment's `diff(..., "months", true)` over date-only values."""
    if earlier.day < later.day:
        return -_month_diff(later, earlier)

    whole = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    anchor = _add_months(earlier, whole)
    if later < anchor:
        previous = _add_months(earlier, whole - 1)
        adjust = (later - anchor).days / (anchor - previous).days
    else:
        following = _add_months(earlier, whole + 1)
        adjust = (later - anchor).days / (following - anchor).days
    return whole + adjust


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def build_sleep_minutes(
    intervals: Iterable[FirebaseSleepIntervalData],
    day_start: datetime,
    day_end: datetime,
) -> set[int]:
    """Mark sleep minutes for one local day from absolute interval timestamps."""
    day_start_ts = day_start.timestamp()
    day_end_ts = day_end.timestamp()
    minutes: set[int] = set()

    for interval in intervals:
        start = max(float(interval.start), day_start_ts)
        end = min(float(interval.start) + float(interval.duration), day_end_ts)
        if end <= start:
            continue

        # The app marks from floor(start) for round(duration) minutes.
        first = math.floor((start - day_start_ts) / 60)
        length = round((end - start) / 60)
        for minute in range(first, first + length):
            if 0 <= minute <= _LAST_MINUTE:
                minutes.add(minute)

    return minutes


def sleep_stats_from_minutes(
    sleep_minutes: set[int],
    *,
    morning_cutoff: float,
    night_start: float,
) -> DaySleepStats:
    """Port of the app's `getSleepStats` over one day's sleep bitmap."""
    morning_cutoff_minutes = 60 * morning_cutoff
    night_end_minutes = 60 * (night_start + 12)

    stats = DaySleepStats()
    state = 0
    run_start = 0
    block_start = -1
    last_sleep: int | None = None

    for minute in range(30, 24 * 60):
        in_sleep = minute in sleep_minutes
        window_start = max(minute - 30, 0)
        window = minute - window_start
        average = sum(1 for value in range(window_start, minute) if value in sleep_minutes) / window

        if in_sleep:
            last_sleep = minute
        if in_sleep and minute - 1 not in sleep_minutes:
            run_start = minute

        if state == 0:
            if average > 0.0001:
                state = 1
                if minute < night_end_minutes:
                    block_start = run_start
                    stats.naps.append(SleepSpan(block_start))
                elif stats.night_start is None and block_start < night_end_minutes:
                    block_start = run_start
                    stats.night_start = block_start
                stats.linked_sleep.append(SleepSpan(run_start))
        elif state == 1:
            assert last_sleep is not None
            half = (last_sleep + 1 - block_start) / 2
            block_before_cutoff = block_start < morning_cutoff_minutes
            midpoint_before_cutoff = block_start + half < morning_cutoff_minutes

            if 0.0001 < average < 1 and midpoint_before_cutoff and not in_sleep:
                stats.morning_wake = last_sleep

            if average <= 0.0001:
                state = 0
                if block_before_cutoff and midpoint_before_cutoff:
                    stats.naps.clear()
                    stats.morning_wake = last_sleep
                    stats.last_sleep = last_sleep
                elif block_start + half < night_end_minutes:
                    stats.last_sleep = last_sleep
                    if stats.naps:
                        stats.naps[-1].end = last_sleep
                elif stats.night_start is None:
                    if stats.naps:
                        stats.naps.pop()
                    stats.night_start = block_start

                if stats.linked_sleep:
                    stats.linked_sleep[-1].end = last_sleep
        else:
            state = 0

    if stats.naps and stats.naps[-1].end is None:
        stats.naps.pop()
        stats.night_start = block_start
    if stats.linked_sleep and stats.linked_sleep[-1].end is None:
        stats.linked_sleep[-1].end = 24 * 60

    stats.linked_naps = _merge_linked_naps(stats.naps)
    if stats.linked_naps:
        stats.nap_type = len(stats.linked_naps)
        stats.wake_windows = _wake_windows_minutes(stats)

    return stats


def find_best_age_match(
    bands: Sequence[FirebaseSettingsNapWindow],
    nap_count: int,
    age: int,
) -> FirebaseSettingsNapWindow | None:
    """Port of the app's `findBestAgeMatch` for `napWindow`."""
    selected: FirebaseSettingsNapWindow | None = None
    for band in bands:
        if _band_gaps(band, nap_count) is None:
            continue

        in_band = band.lowerAge <= age <= band.upperAge
        if selected is None:
            selected = band
            if in_band:
                return band
            continue

        if in_band:
            return band

        selected_midpoint = (selected.lowerAge + selected.upperAge) / 2
        band_midpoint = (band.lowerAge + band.upperAge) / 2
        if abs(age - band_midpoint) < abs(age - selected_midpoint):
            selected = band

    return selected


def default_wake_windows(
    settings: FirebaseSettingsPlusDocument,
    nap_count: int,
    age: int,
) -> WakeWindowConfig | None:
    """Return the age-band defaults for one nap schedule."""
    if not settings.napWindow:
        return None

    band = find_best_age_match(settings.napWindow, nap_count, age)
    if band is None:
        return None

    gaps = _band_gaps(band, nap_count)
    if gaps is None:
        return None

    return WakeWindowConfig(
        gaps=gaps,
        extra_naps=_band_number_map(band, "extraNaps"),
        limits=_band_number_map(band, "limits"),
        lower_age=band.lowerAge,
        upper_age=band.upperAge,
        original_values=list(gaps),
        source=SOURCE_DEFAULT,
    )


def user_wake_windows(
    child: FirebaseChildDocument,
    settings: FirebaseSettingsPlusDocument,
    *,
    nap_count: int,
    age: int,
    now: datetime,
    history: Sequence[DaySleepStats] | None = None,
) -> WakeWindowConfig | None:
    """Port of the app's `getUserWakeWindows` without Firebase writes."""
    config = default_wake_windows(settings, nap_count, age)
    if config is None:
        return None

    custom = child.customNapWindow
    if (
        custom is not None
        and custom.lowerAge is not None
        and custom.upperAge is not None
        and custom.lowerAge <= age <= custom.upperAge
    ):
        custom_gaps = _custom_gaps(custom, nap_count)
        if custom_gaps is not None:
            config.lower_age = custom.lowerAge
            config.upper_age = custom.upperAge
            config.original_values = list(custom_gaps)
            config.gaps = list(custom_gaps)
            config.source = SOURCE_SC_CUSTOM

    skip_transition = False
    adjust = child.sweetspotAdjust
    if adjust is not None and adjust.custom and adjust.windows is not None:
        windows = adjust.windows.get(str(nap_count))
        if windows is not None:
            adjusted = False
            for index in range(nap_count + 1):
                window = windows.get(str(index))
                if window is None:
                    continue
                if window.manual and isinstance(window.absolute, (int, float)):
                    config.gaps[index] = float(window.absolute)
                    if window.absolute != 0:
                        adjusted = True
                    config.source = SOURCE_USER_ADJUST_ABSOLUTE
                elif isinstance(window.relative, (int, float)):
                    config.gaps[index] += float(window.relative) / 60
                    config.gaps[index] = max(config.gaps[index], 0)
                    if window.relative != 0:
                        adjusted = True
                    if config.source == SOURCE_SC_CUSTOM:
                        config.source = SOURCE_USER_ADJUST_RELATIVE_DEFAULT
            if adjusted and nap_count == 2:
                skip_transition = True

    if not skip_transition:
        _apply_calculated_wake_windows(
            child,
            settings,
            config,
            nap_count=nap_count,
            age=age,
            now=now,
            history=history,
        )

    return config


def calculate_next_nap(
    child: FirebaseChildDocument,
    settings: FirebaseSettingsPlusDocument,
    *,
    today_stats: DaySleepStats,
    nap_count: int,
    age: int,
    now: datetime,
    timezone: tzinfo,
    history: Sequence[DaySleepStats] | None = None,
) -> NextNapResult | None:
    """Port of the app's `getNextNap` for one nap schedule."""
    config = user_wake_windows(
        child,
        settings,
        nap_count=nap_count,
        age=age,
        now=now,
        history=history,
    )
    if config is None or not config.gaps:
        return None

    cutoff = child_morning_cutoff(child)
    bedtime = child_night_start(child)
    index_base = _index_base_seconds(today_stats)
    if index_base is None:
        return None

    nap_num = _nap_index(today_stats, index_base, nap_count, cutoff, bedtime)
    real_nap_num = nap_num
    if nap_num >= len(config.gaps) - 1:
        nap_num = len(config.gaps) - 1

    wake_window = config.gaps[nap_num]
    if real_nap_num > nap_num and config.extra_naps is not None:
        extra = config.extra_naps.get(str(nap_count))
        if extra:
            offset = real_nap_num - nap_num
            wake_window = extra[offset] if offset < len(extra) else extra[-1]

    source = config.source
    allow_replacement = _allows_replacement(child, age)
    displayed = _displayed_window(child, nap_count, nap_num)
    if displayed is not None and allow_replacement:
        timestamp, value, displayed_source = displayed
        if _is_same_local_day(timestamp, now, timezone):
            wake_window = value
            source = displayed_source or source

    if nap_count == 0:
        base_seconds = 60 * today_stats.morning_wake if today_stats.morning_wake is not None else 3600 * cutoff
    else:
        base_seconds = index_base
        base_seconds = _last_linked_sleep_seconds(
            today_stats,
            base_seconds,
            night_start=bedtime,
        )

    if _should_shorten_wake_window(child, today_stats, nap_count, nap_num, now):
        wake_window *= 0.8

    return NextNapResult(
        next_nap=base_seconds + 3600 * wake_window,
        wake_window=wake_window,
        source=source,
        nap_num=nap_num,
        nap_count=nap_count,
    )


def round_to_display_time(timestamp: float) -> int:
    """Round an absolute Unix timestamp to the app's nearest 10 minutes."""
    return int(10 * round(timestamp / 600) * 60)


def calculate_wake_windows(
    age: int,
    history: Sequence[DaySleepStats],
    settings: FirebaseSettingsPlusDocument,
    *,
    nap_counts: Sequence[int],
) -> tuple[bool, dict[int, list[float]]]:
    """Port of the app's `calculatedWakeWindows` (read-only)."""
    calculated = [_aggregate_wake_windows(history, days) for days in (7, 14, 30)]
    averages = [_average_wake_windows(aggregate) for aggregate in calculated]
    rounded = [_round_15_windows(average) for average in averages]

    counts = {nap_count: [0, 0, 0] for nap_count in (2, 3, 4, 5)}
    for nap_count in counts:
        counts[nap_count][0] = _sample_count(calculated[0], nap_count, 2)
        counts[nap_count][1] = _sample_count(calculated[1], nap_count, 4)
        counts[nap_count][2] = _sample_count(calculated[2], nap_count, 8)

    extracted: dict[int, list[float]] = {}
    for nap_count in (2, 3, 4, 5):
        if counts[nap_count][0] >= 3 or counts[nap_count][1] >= 7 or counts[nap_count][2] >= 15:
            if counts[nap_count][1] >= 7:
                extracted[nap_count] = [value / 60 for value in rounded[1][nap_count]]
            elif counts[nap_count][2] >= 15:
                extracted[nap_count] = [value / 60 for value in rounded[2][nap_count]]
            elif counts[nap_count][0] >= 3:
                extracted[nap_count] = [value / 60 for value in rounded[0][nap_count]]

    transition = 2 in nap_counts and age < 9 and _has_samples(counts.get(3)) and not _has_samples(counts.get(2))

    if transition:
        if counts[3][0] == 3 and counts[3][1] < 7 and counts[3][2] < 15:
            extracted[3] = []
        extracted[2] = _create_transition_wake_window(age, extracted, settings)

    return transition, extracted


def _create_transition_wake_window(
    age: int,
    extracted: dict[int, list[float]],
    settings: FirebaseSettingsPlusDocument,
) -> list[float]:
    defaults_2 = default_wake_windows(settings, 2, age)
    defaults_3 = default_wake_windows(settings, 3, age)
    if defaults_2 is None or defaults_3 is None:
        return []

    gaps: list[float] = []
    extracted_3 = extracted.get(3) or []
    if len(extracted_3) > 3:
        for index in (0, 1):
            gaps.append(_transition_gap(extracted_3[index], defaults_2.gaps[index], defaults_3.gaps[index]))
        gaps.append(_transition_gap(extracted_3[3], defaults_2.gaps[2], defaults_3.gaps[3]))
    else:
        gaps = [
            60 * (defaults_2.gaps[0] + defaults_3.gaps[0]) / 2,
            60 * (defaults_2.gaps[1] + defaults_3.gaps[1]) / 2,
            60 * (defaults_2.gaps[2] + defaults_3.gaps[3]) / 2,
        ]

    return [value / 60 for value in _round_15_minutes(gaps)]


def _transition_gap(extracted: float, default_2: float, default_3: float) -> float:
    if extracted > default_2:
        return 60 * default_2
    value = 60 * (extracted + default_2) / 2
    return min(value, 60 * default_3)


def _apply_calculated_wake_windows(
    child: FirebaseChildDocument,
    settings: FirebaseSettingsPlusDocument,
    config: WakeWindowConfig,
    *,
    nap_count: int,
    age: int,
    now: datetime,
    history: Sequence[DaySleepStats] | None,
) -> None:
    calculated = child.calculatedWakeWindows or {}
    stored = calculated.get(str(nap_count))
    if stored is not None and not _is_stale(stored.timestamp, now) and stored.transition and age < 9:
        if stored.wakeWindow:
            config.gaps = [float(value) for value in stored.wakeWindow]
            config.source = SOURCE_TRANSITION
        return

    if history is None:
        return

    transition, extracted = calculate_wake_windows(
        age,
        history,
        settings,
        nap_counts=user_nap_counts(child.naps),
    )
    if not transition or nap_count != 2 or 2 not in extracted:
        return

    gaps = list(extracted[2])
    stored = calculated.get("2")
    if stored is not None and stored.wakeWindow:
        gaps = _max_windows(gaps, [float(value) for value in stored.wakeWindow])

    config.gaps = gaps
    config.source = SOURCE_TRANSITION


def _aggregate_wake_windows(history: Sequence[DaySleepStats], days: int) -> dict[str, Any]:
    selected = history[-days:] if days else []
    wake_windows: dict[str, dict[int, list[float]]] = {}
    for stats in selected:
        if not stats.wake_windows or stats.nap_type is None:
            continue
        nap_type = str(stats.nap_type)
        bucket = wake_windows.setdefault(nap_type, {})
        for index, value in enumerate(stats.wake_windows):
            bucket.setdefault(index, [])
            if value:
                bucket[index].append(value)

    return {"wakeWindows": wake_windows, "daysInCalculation": len(selected)}


def _average_wake_windows(aggregate: dict[str, Any]) -> dict[int, list[float]]:
    wake_windows: dict[str, dict[int, list[float]]] = aggregate["wakeWindows"]
    days = aggregate["daysInCalculation"] or math.inf
    averages: dict[int, list[float]] = {}

    for nap_type, bucket in wake_windows.items():
        nap_count = int(nap_type)
        values = [sum(bucket[index]) / len(bucket[index]) if bucket[index] else 0.0 for index in sorted(bucket)]
        if nap_count < 6:
            if sum(1 for value in values if value > 0) > 1:
                averages[nap_count] = values
        elif nap_count == 6:
            first = bucket.get(0, [])
            if first and len(first) / days >= 2 / 7:
                if sum(1 for value in values if value > 0) > 1:
                    averages[nap_count] = values
    return averages


def _sample_count(aggregate: dict[str, Any], nap_count: int, minimum: int) -> int:
    bucket = aggregate["wakeWindows"].get(str(nap_count))
    if not bucket:
        return 0
    first = bucket.get(0)
    if first is not None and len(first) >= minimum:
        return len(first)
    return 0


def _has_samples(counts: list[int] | None) -> bool:
    return counts is not None and (counts[0] >= 3 or counts[1] >= 7 or counts[2] >= 15)


def _round_15_windows(values: dict[int, list[float]]) -> dict[int, list[float]]:
    return {nap_count: _round_15_minutes(gaps) for nap_count, gaps in values.items()}


def _round_15_minutes(values: Sequence[float]) -> list[float]:
    rounded: list[float] = []
    for value in values:
        hours = math.trunc(value / 60)
        remainder = value % 60
        quarters = math.trunc(remainder / 15)
        leftover = remainder % 15
        if leftover >= 7.5:
            quarters += 1
        rounded.append(60 * hours + 15 * quarters)
    return rounded


def _max_windows(left: list[float], right: list[float]) -> list[float]:
    return [max(a, b) for a, b in zip(left, right)]


def _merge_linked_naps(naps: Sequence[SleepSpan]) -> list[SleepSpan]:
    linked: list[SleepSpan] = []
    if not naps:
        return linked
    if len(naps) == 1:
        return [SleepSpan(naps[0].start, naps[0].end)]

    current = SleepSpan(naps[0].start, naps[0].end)
    for index, nap in enumerate(naps[1:], start=1):
        if nap.start > (current.end if current.end is not None else nap.start) + 30:
            linked.append(current)
            current = SleepSpan(nap.start, nap.end)
        else:
            current.end = nap.end
        if index == len(naps) - 1:
            linked.append(current)
    return linked


def _wake_windows_minutes(stats: DaySleepStats) -> list[float]:
    linked = stats.linked_naps
    if not linked or stats.morning_wake is None or stats.night_start is None:
        return []

    windows: list[float] = []
    for index, nap in enumerate(linked):
        if nap.end is None:
            return []
        if index == 0:
            windows.append(nap.start - stats.morning_wake)
            next_start = stats.night_start if len(linked) == 1 else linked[index + 1].start
            windows.append(next_start - nap.end)
        else:
            next_start = stats.night_start if index == len(linked) - 1 else linked[index + 1].start
            windows.append(next_start - nap.end)
    return windows


def _index_base_seconds(stats: DaySleepStats) -> float | None:
    if stats.last_sleep is not None:
        return 60 * stats.last_sleep
    if stats.morning_wake is not None:
        return 60 * stats.morning_wake
    return None


def _nap_index(
    stats: DaySleepStats,
    base_seconds: float,
    nap_count: int,
    morning_cutoff: float,
    night_start: float,
) -> int:
    if stats.morning_wake is not None:
        return len(stats.naps)

    day_span = 60 * (night_start + 12 - morning_cutoff) * 60
    fraction = (base_seconds - 60 * morning_cutoff * 60) / day_span
    fraction = min(max(fraction, 0), 1)
    return math.ceil(fraction * nap_count)


def _last_linked_sleep_seconds(stats: DaySleepStats, base_seconds: float, *, night_start: float) -> float:
    if not stats.linked_sleep:
        return base_seconds
    end = stats.linked_sleep[-1].end
    if end is None:
        return base_seconds
    end_seconds = 60 * end
    if end_seconds > base_seconds and end < 60 * (night_start + 12) + 30:
        return end_seconds
    return base_seconds


def _displayed_window(
    child: FirebaseChildDocument,
    nap_count: int,
    nap_num: int,
) -> tuple[float, float, str | None] | None:
    sweetspot = child.sweetspot
    if sweetspot is None or sweetspot.displayedSweetSpot is None:
        return None
    entry = sweetspot.displayedSweetSpot.get(str(nap_count), {}).get(f"W{nap_num + 1}")
    if entry is None or entry.timestamp is None or entry.value is None:
        return None
    return float(entry.timestamp), float(entry.value), entry.source


def _allows_replacement(child: FirebaseChildDocument, age: int) -> bool:
    if child.sweetspotAdjust is not None and child.sweetspotAdjust.custom:
        return False
    custom = child.customNapWindow
    if custom is None or custom.lowerAge is None or custom.upperAge is None:
        return True
    return not (custom.lowerAge <= age <= custom.upperAge)


def _should_shorten_wake_window(
    child: FirebaseChildDocument,
    stats: DaySleepStats,
    nap_count: int,
    nap_num: int,
    now: datetime,
) -> bool:
    if not stats.naps:
        return False
    last_nap = stats.naps[-1]
    if last_nap.end is None:
        return False
    if last_nap.end - last_nap.start + 1 >= 30:
        return False
    if _is_custom_and_absolute(child, nap_count, nap_num):
        return False
    if _is_ai_ml_available(child, nap_count, nap_num, now):
        return False
    return True


def _is_custom_and_absolute(child: FirebaseChildDocument, nap_count: int, nap_num: int) -> bool:
    adjust = child.sweetspotAdjust
    if adjust is None or not adjust.custom or adjust.windows is None:
        return False
    window = adjust.windows.get(str(nap_count), {}).get(str(nap_num))
    if window is None:
        return False
    return bool(window.manual and isinstance(window.absolute, (int, float)) and window.absolute > 0)


def _is_ai_ml_available(child: FirebaseChildDocument, nap_count: int, nap_num: int, now: datetime) -> bool:
    sweetspot = child.sweetspot
    if sweetspot is None or sweetspot.ai is None:
        return False
    entry = sweetspot.ai.get(str(nap_count), {}).get(f"W{nap_num + 1}")
    if entry is None:
        return False
    return entry.source == "ai_ml" and (entry.best_by_date or 0) > now.timestamp()


def _is_same_local_day(timestamp: float, now: datetime, timezone: tzinfo) -> bool:
    return datetime.fromtimestamp(timestamp, timezone).date() == now.date()


def _is_stale(timestamp: Number | None, now: datetime) -> bool:
    if timestamp is None:
        return True
    value = datetime.fromtimestamp(float(timestamp), now.tzinfo).date()
    return (now.date() - value).total_seconds() / 86400 > 3.5


def child_morning_cutoff(child: FirebaseChildDocument) -> float:
    value = child.morningCutoff
    if isinstance(value, (int, float)) and 4 < value < 12:
        return float(value)
    return 8.0


def child_night_start(child: FirebaseChildDocument) -> float:
    value = child.nightStart
    if isinstance(value, (int, float)) and 3 < value < 11:
        return float(value)
    return 6.0


def _band_gaps(band: FirebaseSettingsNapWindow, nap_count: int) -> list[float] | None:
    value = (band.model_extra or {}).get(str(nap_count))
    if not isinstance(value, list) or not value:
        return None
    gaps = [float(item) for item in value if isinstance(item, (int, float)) and not isinstance(item, bool)]
    return gaps or None


def _band_number_map(band: FirebaseSettingsNapWindow, field_name: str) -> dict[str, list[float]] | None:
    value = getattr(band, field_name)
    if not isinstance(value, dict):
        return None
    mapped: dict[str, list[float]] = {}
    for key, items in value.items():
        if isinstance(items, list):
            mapped[str(key)] = [
                float(item) for item in items if isinstance(item, (int, float)) and not isinstance(item, bool)
            ]
    return mapped or None


def _custom_gaps(custom: FirebaseChildCustomNapWindow, nap_count: int) -> list[float] | None:
    value = (custom.model_extra or {}).get(str(nap_count))
    if not isinstance(value, list) or not value:
        return None
    gaps = [float(item) for item in value if isinstance(item, (int, float)) and not isinstance(item, bool)]
    return gaps or None


def _parse_birthdate(value: str | Number | None) -> date | None:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
            except ValueError:
                return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 100_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, dt_timezone.utc).date()
        except OverflowError, OSError, ValueError:
            return None
    return None
