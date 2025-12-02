"""Type definitions for Huckleberry API."""
from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

# Literal type aliases for enums
DiaperMode = Literal["pee", "poo", "both", "dry"]
PooColor = Literal["yellow", "green", "brown", "black", "red"]
PooConsistency = Literal["runny", "soft", "solid", "hard"]
FeedMode = Literal["breast", "bottle", "solids"]
FeedSide = Literal["left", "right", "none"]
GenderType = Literal["boy", "girl", "other"]
UnitsSystem = Literal["metric", "imperial"]
WeightUnits = Literal["kg", "lbs"]
HeightUnits = Literal["cm", "inches"]
HeadUnits = Literal["hcm", "hinches"]  # head cm, head inches


class ChildData(TypedDict):
    """Child profile data structure from Firestore.

    Collection: childs/{child_id}

    Firebase Field Mapping (camelCase → snake_case):
    - uid → uid
    - name → name
    - birthdate → birthday (YYYY-MM-DD format string)
    - picture → picture (Firebase Storage URL)
    - gender → gender ("boy", "girl", or other)
    - color → color (hex color for UI)
    - createdAt → created_at
    - nightStart → night_start_min (minutes from midnight)
    - morningCutoff → morning_cutoff_min (minutes from midnight)
    - expectedNaps → expected_naps (count)
    - categories → categories
    """
    uid: str
    name: str
    birthday: NotRequired[str | None]
    picture: NotRequired[str | None]
    gender: NotRequired[GenderType | None]
    color: NotRequired[str | None]
    created_at: NotRequired[Any]
    night_start_min: NotRequired[int | None]
    morning_cutoff_min: NotRequired[int | None]
    expected_naps: NotRequired[int | None]
    categories: NotRequired[list[str] | None]


class GrowthData(TypedDict):
    """Growth measurement data structure.

    Used for logging weight, height, and head circumference measurements.
    Stored in health/{child_uid}/data subcollection (NOT intervals!).

    Units:
    - weight_units: "kg" or "lbs"
    - height_units: "cm" or "inches"
    - head_units: "hcm" (head cm) or "hinches" (head inches)
    """
    weight: NotRequired[float | None]
    height: NotRequired[float | None]
    head: NotRequired[float | None]
    weight_units: WeightUnits
    height_units: HeightUnits
    head_units: HeadUnits
    timestamp_sec: NotRequired[float | None]


class SleepTimerData(TypedDict):
    """Sleep timer data structure from Firestore.

    Collection: sleep/{child_uid}
    Field: timer

    CRITICAL: timer_start_time_ms is in MILLISECONDS (multiply time.time() by 1000)
    This is different from feeding which uses seconds!

    Firebase Field Mapping (camelCase → snake_case):
    - active → active
    - paused → paused
    - timestamp → timestamp (Firestore server timestamp)
    - local_timestamp_sec → local_timestamp_sec
    - timerStartTime → timer_start_time_ms (MILLISECONDS!)
    - uuid → uuid (16-char hex)
    - details → details
    """
    active: bool
    paused: bool
    timestamp: NotRequired[dict[str, float]]
    local_timestamp_sec: NotRequired[float]
    timer_start_time_ms: NotRequired[float | None]
    uuid: NotRequired[str]
    details: NotRequired[dict[str, Any]]


class SleepDocumentData(TypedDict):
    """Complete sleep document structure from Firestore.

    Collection: sleep/{child_uid}

    Structure:
    - timer: Current sleep session state
    - prefs: Last completed sleep and preferences

    Intervals: sleep/{child_uid}/intervals/{interval_id}
    """
    timer: NotRequired[SleepTimerData]
    prefs: NotRequired[dict[str, Any]]


class FeedTimerData(TypedDict):
    """Feed timer data structure from Firestore.

    Collection: feed/{child_uid}
    Field: timer

    CRITICAL: timer_start_time_sec is in SECONDS (use time.time() directly)
    This is different from sleep which uses milliseconds!
    Also note: timer_start_time_sec RESETS on every side switch and resume.

    Firebase Field Mapping (camelCase → snake_case):
    - active → active
    - paused → paused
    - timestamp → timestamp (Firestore server timestamp)
    - local_timestamp_sec → local_timestamp_sec
    - feedStartTime → feed_start_time_sec (absolute session start, seconds)
    - timerStartTime → timer_start_time_sec (resets on switch/resume, seconds)
    - uuid → uuid (16-char hex)
    - leftDuration → left_duration_sec (accumulated seconds)
    - rightDuration → right_duration_sec (accumulated seconds)
    - lastSide → last_side ("left", "right", "none")
    - activeSide → active_side (current side, used by home page)
    """
    active: bool
    paused: bool
    timestamp: NotRequired[dict[str, float]]
    local_timestamp_sec: NotRequired[float]
    feed_start_time_sec: NotRequired[float]
    timer_start_time_sec: NotRequired[float]
    uuid: NotRequired[str]
    left_duration_sec: NotRequired[float]
    right_duration_sec: NotRequired[float]
    last_side: NotRequired[FeedSide]
    active_side: NotRequired[FeedSide]


class FeedDocumentData(TypedDict):
    """Complete feed document structure from Firestore.

    Collection: feed/{child_uid}

    Structure:
    - timer: Current feeding session state
    - prefs: Last completed feeding and preferences

    Intervals: feed/{child_uid}/intervals/{interval_id}
    """
    timer: NotRequired[FeedTimerData]
    prefs: NotRequired[dict[str, Any]]


class DiaperData(TypedDict):
    """Diaper change data structure.

    Used for logging diaper changes (instant events, no timer).
    Stored in diaper/{child_uid}/intervals subcollection.

    Modes:
    - "pee": Pee only
    - "poo": Poo only
    - "both": Both pee and poo
    - "dry": Dry check (no change needed)

    Firebase Field Mapping (camelCase → snake_case):
    - mode → mode ("pee", "poo", "both", "dry")
    - start → start_sec (timestamp seconds)
    - lastUpdated → last_updated_sec (timestamp seconds)
    - offset → offset_min (timezone minutes)
    - quantity → quantity (dict with "pee"/"poo" amounts)
    - color → color (poo color)
    - consistency → consistency (poo consistency)
    """
    mode: DiaperMode
    start_sec: float
    last_updated_sec: float
    offset_min: float
    quantity: NotRequired[dict[str, float]]
    color: NotRequired[PooColor]
    consistency: NotRequired[PooConsistency]


class DiaperDocumentData(TypedDict):
    """Complete diaper document structure from Firestore.

    Collection: diaper/{child_uid}

    Structure:
    - prefs: Last diaper change and reminder settings

    Intervals: diaper/{child_uid}/intervals/{interval_id}
    Note: Unlike sleep/feed, no timer field (instant events only)
    """
    prefs: NotRequired[dict[str, Any]]


class HealthDocumentData(TypedDict):
    """Complete health document structure from Firestore.

    Collection: health/{child_uid}

    Structure:
    - prefs: Last measurements and preferences

    CRITICAL: Health uses "data" subcollection, NOT "intervals"!
    Subcollection: health/{child_uid}/data/{data_id}

    This is the ONLY tracker that uses "data" instead of "intervals".
    All others (sleep, feed, diaper, pump, solids, activities) use "intervals".
    """
    prefs: NotRequired[dict[str, Any]]


class SleepIntervalData(TypedDict):
    """Sleep interval entry data structure.

    Collection: sleep/{child_uid}/intervals/{interval_id}

    Document ID format: {timestamp_ms}-{random_20_chars}
    Example: "1764528069548-a04ff18de85c4a98a451"

    Firebase Field Mapping (camelCase → snake_case):
    - start → start_sec (timestamp seconds)
    - duration → duration_sec (seconds)
    - offset → offset_min (timezone minutes, negative for UTC-)
    - end_offset → end_offset_min (timezone minutes)
    - details → details (sleep conditions and locations)
    - lastUpdated → last_updated_sec (timestamp seconds)
    """
    start_sec: float
    duration_sec: float
    offset_min: float
    end_offset_min: NotRequired[float]
    details: NotRequired[dict[str, Any]]
    last_updated_sec: NotRequired[float]


class FeedIntervalData(TypedDict):
    """Feed interval entry data structure.

    Collection: feed/{child_uid}/intervals/{interval_id}

    Document ID format: {timestamp_ms}-{random_20_chars}
    Example: "1764528069548-a04ff18de85c4a98a451"

    Firebase Field Mapping (camelCase → snake_case):
    - mode → mode ("breast", "bottle", "solids")
    - start → start_sec (timestamp seconds)
    - lastSide → last_side ("left", "right", "none")
    - lastUpdated → last_updated_sec (timestamp seconds)
    - leftDuration → left_duration_sec (seconds)
    - rightDuration → right_duration_sec (seconds)
    - offset → offset_min (timezone minutes)
    - end_offset → end_offset_min (timezone minutes)
    """
    mode: FeedMode
    start_sec: float
    last_side: FeedSide
    last_updated_sec: float
    left_duration_sec: NotRequired[float]
    right_duration_sec: NotRequired[float]
    offset_min: float
    end_offset_min: NotRequired[float]
