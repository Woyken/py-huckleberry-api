"""Type definitions for Huckleberry API."""
from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

# Literal type aliases for enums
DiaperMode = Literal["pee", "poo", "both", "dry"]
PooColor = Literal["yellow", "brown", "black", "green", "red", "gray"]
PooConsistency = Literal["solid", "loose", "runny", "mucousy", "hard", "pebbles", "diarrhea"]
FeedMode = Literal["breast", "bottle", "solids"]
FeedSide = Literal["left", "right", "none"]
BottleType = Literal["Breast Milk", "Formula", "Mixed"]
VolumeUnits = Literal["ml", "oz"]
GenderType = Literal["boy", "girl", "other"]
UnitsSystem = Literal["metric", "imperial"]
WeightUnits = Literal["kg", "lbs"]
HeightUnits = Literal["cm", "in"]
HeadUnits = Literal["hcm", "hin"]  # head cm, head inches


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


class LastSleepData(TypedDict):
    """Data for prefs.lastSleep."""
    start: float
    duration: float
    offset: float


class SleepPrefs(TypedDict):
    """Preferences structure for sleep."""
    lastSleep: NotRequired[LastSleepData]
    timestamp: NotRequired[FirebaseTimestamp]
    local_timestamp: NotRequired[float]


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
    details: NotRequired[FirebaseSleepDetails]


class SleepDocumentData(TypedDict):
    """Complete sleep document structure from Firestore.

    Collection: sleep/{child_uid}

    Structure:
    - timer: Current sleep session state
    - prefs: Last completed sleep and preferences

    Intervals: sleep/{child_uid}/intervals/{interval_id}
    """
    timer: NotRequired[SleepTimerData]
    prefs: NotRequired[SleepPrefs]


class LastNursingData(TypedDict):
    """Data for prefs.lastNursing."""
    mode: FeedMode
    start: float
    duration: float
    leftDuration: float
    rightDuration: float
    offset: float


class LastSideData(TypedDict):
    """Data for prefs.lastSide."""
    start: float
    lastSide: FeedSide


class LastBottleData(TypedDict):
    """Data for prefs.lastBottle."""
    mode: Literal["bottle"]
    start: float
    bottleType: BottleType
    bottleAmount: float
    bottleUnits: VolumeUnits
    offset: float


class FeedPrefs(TypedDict):
    """Preferences structure for feeding."""
    lastNursing: NotRequired[LastNursingData]
    lastSide: NotRequired[LastSideData]
    timestamp: NotRequired[FirebaseTimestamp]
    local_timestamp: NotRequired[float]


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
    prefs: NotRequired[FeedPrefs]


class LastDiaperData(TypedDict):
    """Data for prefs.lastDiaper."""
    start: float
    mode: DiaperMode
    offset: float


class DiaperPrefs(TypedDict):
    """Preferences structure for diaper."""
    lastDiaper: NotRequired[LastDiaperData]
    timestamp: NotRequired[FirebaseTimestamp]
    local_timestamp: NotRequired[float]


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
    prefs: NotRequired[DiaperPrefs]


class HealthPrefs(TypedDict):
    """Preferences structure for health."""
    lastGrowthEntry: NotRequired[FirebaseGrowthData]
    timestamp: NotRequired[FirebaseTimestamp]
    local_timestamp: NotRequired[float]


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
    prefs: NotRequired[HealthPrefs]


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
    details: NotRequired[FirebaseSleepDetails]
    last_updated_sec: NotRequired[float]


class BreastFeedIntervalData(TypedDict):
    """Breast feeding interval data structure.

    Collection: feed/{child_uid}/intervals/{interval_id}
    Mode: "breast"

    Firebase Field Mapping (camelCase → snake_case):
    - mode → mode ("breast")
    - start → start_sec (timestamp seconds)
    - lastSide → last_side ("left", "right", "none")
    - lastUpdated → last_updated_sec (timestamp seconds)
    - leftDuration → left_duration_sec (seconds)
    - rightDuration → right_duration_sec (seconds)
    - offset → offset_min (timezone minutes)
    - end_offset → end_offset_min (timezone minutes)
    """
    mode: Literal["breast"]
    start_sec: float
    last_side: FeedSide
    last_updated_sec: float
    left_duration_sec: float
    right_duration_sec: float
    offset_min: float
    end_offset_min: NotRequired[float]


class BottleFeedIntervalData(TypedDict):
    """Bottle feeding interval data structure.

    Collection: feed/{child_uid}/intervals/{interval_id}
    Mode: "bottle"

    Firebase Field Mapping (camelCase → snake_case):
    - mode → mode ("bottle")
    - start → start_sec (timestamp seconds)
    - lastUpdated → last_updated_sec (timestamp seconds)
    - bottleType → bottle_type ("Breast Milk", "Formula", "Mixed")
    - amount → amount (volume)
    - units → units ("ml", "oz")
    - offset → offset_min (timezone minutes)
    - end_offset → end_offset_min (timezone minutes)
    """
    mode: Literal["bottle"]
    start_sec: float
    last_updated_sec: float
    bottle_type: BottleType
    amount: float
    units: VolumeUnits
    offset_min: float
    end_offset_min: NotRequired[float]


class SolidsFeedIntervalData(TypedDict):
    """Solid food feeding interval data structure.

    Collection: feed/{child_uid}/intervals/{interval_id}
    Mode: "solids"

    Note: Solids structure not yet fully implemented.
    """
    mode: Literal["solids"]
    start_sec: float
    last_updated_sec: float
    offset_min: float
    end_offset_min: NotRequired[float]


# Union type for all feed interval types
FeedIntervalData = BreastFeedIntervalData | BottleFeedIntervalData | SolidsFeedIntervalData


# --- Firebase Raw Types (camelCase) ---
# These types match the exact structure stored in Firestore.
# Use these when constructing payloads for set() or update().

class FirebaseTimestamp(TypedDict):
    """Firestore timestamp structure."""
    seconds: float
    nanos: NotRequired[int]


class FirebaseSleepCondition(TypedDict):
    """Sleep start/end conditions."""
    happy: NotRequired[bool]
    longTimeToFallAsleep: NotRequired[bool]
    upset: NotRequired[bool]
    wokeUpChild: NotRequired[bool]
    # Dynamic keys like "10-20_minutes" are possible but hard to type strictly
    # We can use NotRequired for known ones
    under_10_minutes: NotRequired[bool]


class FirebaseSleepLocations(TypedDict):
    """Sleep locations."""
    car: NotRequired[bool]
    nursing: NotRequired[bool]
    wornOrHeld: NotRequired[bool]
    stroller: NotRequired[bool]
    coSleep: NotRequired[bool]
    nextToCarer: NotRequired[bool]
    onOwnInBed: NotRequired[bool]
    bottle: NotRequired[bool]
    swing: NotRequired[bool]


class FirebaseSleepDetails(TypedDict):
    """Sleep details structure."""
    startSleepCondition: NotRequired[dict[str, bool]]
    sleepLocations: NotRequired[FirebaseSleepLocations]
    endSleepCondition: NotRequired[dict[str, bool]]


class FirebaseSleepTimer(TypedDict):
    """Raw sleep timer structure (camelCase)."""
    active: bool
    paused: bool
    timestamp: FirebaseTimestamp
    local_timestamp: float
    timerStartTime: float | None  # Milliseconds!
    uuid: str
    details: NotRequired[FirebaseSleepDetails]


class FirebaseSleepDocument(TypedDict):
    """Raw sleep document structure."""
    timer: NotRequired[FirebaseSleepTimer]
    prefs: NotRequired[dict[str, Any]]


class FirebaseFeedTimer(TypedDict):
    """Raw feed timer structure (camelCase)."""
    active: bool
    paused: bool
    timestamp: FirebaseTimestamp
    local_timestamp: float
    feedStartTime: float  # Seconds
    timerStartTime: float  # Seconds (resets on switch)
    uuid: str
    leftDuration: float
    rightDuration: float
    lastSide: FeedSide
    activeSide: NotRequired[FeedSide]


class FirebaseFeedDocument(TypedDict):
    """Raw feed document structure."""
    timer: NotRequired[FirebaseFeedTimer]
    prefs: NotRequired[dict[str, Any]]


class FirebaseDiaperInterval(TypedDict):
    """Raw diaper interval structure."""
    mode: DiaperMode
    start: float
    lastUpdated: float
    offset: float
    quantity: NotRequired[dict[str, float]]
    color: NotRequired[PooColor]
    consistency: NotRequired[PooConsistency]


class FirebaseBottleInterval(TypedDict):
    """Raw bottle feeding interval structure (camelCase)."""
    mode: Literal["bottle"]
    start: float
    lastUpdated: float
    bottleType: BottleType
    amount: float
    units: VolumeUnits
    offset: float
    end_offset: NotRequired[float]


class FirebaseGrowthData(TypedDict):
    """Raw growth data structure."""
    type: Literal["health"]
    mode: Literal["growth"]
    start: float
    lastUpdated: float
    offset: float
    isNight: bool
    multientry_key: None
    weight: NotRequired[float]
    weightUnits: NotRequired[WeightUnits]
    height: NotRequired[float]
    heightUnits: NotRequired[HeightUnits]
    head: NotRequired[float]
    headUnits: NotRequired[HeadUnits]
