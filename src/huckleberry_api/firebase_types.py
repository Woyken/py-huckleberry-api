"""Strict Pydantic models for Huckleberry Firebase payloads.

This file is the canonical schema reference for Firebase payloads.
Collection and field semantics are documented next to the models that enforce them.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

Number: TypeAlias = int | float
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonMap: TypeAlias = dict[str, JsonValue]

# Additional Firebase paths not yet modeled as strict classes in this file:
# - insights
# - notifications/{uid}/messages
# - recommendations
# - feedback/{uid}
# - health/{child_uid}/types
# - insights/{child_uid}/dailyTips
# - insights/{child_uid}/miniPlans


class StrictModel(BaseModel):
    """Strict base model used for all Firebase schemas.

    Firebase path: shared across all modeled Firebase documents.
    """

    model_config = ConfigDict(extra="ignore", strict=True, populate_by_name=True, protected_namespaces=())


DiaperMode = Literal["pee", "poo", "both", "dry"]
PooColor = Literal["yellow", "brown", "black", "green", "red", "gray"]
PooConsistency = Literal["solid", "loose", "runny", "mucousy", "hard", "pebbles", "diarrhea"]
FeedMode = Literal["breast", "bottle", "solids"]
FeedSide = Literal["left", "right", "none"]
SolidsReaction = Literal["LOVED", "MEH", "HATED", "ALLERGIC"]
SolidsFoodSource = Literal["custom", "curated"]
BottleType = Literal["Breast Milk", "Formula", "Tube Feeding", "Cow Milk", "Goat Milk", "Soy Milk", "Other"]
VolumeUnits = Literal["ml", "oz"]
MedicationUnits = Literal["ml", "oz", "tsp", "drops"]
TemperatureUnits = Literal["C", "F"]
GenderType = Literal["M", "F", ""]
WeightUnits = Literal["kg", "lbs.oz"]
HeightUnits = Literal["cm", "ft.in"]
HeadUnits = Literal["hcm", "hin"]
HealthDataMode = Literal["growth", "medication", "temperature"]
ActivityMode = Literal[
    "bath", "tummyTime", "storyTime", "screenTime", "skinToSkin", "outdoorPlay", "indoorPlay", "brushTeeth"
]
PumpEntryMode = Literal["leftright", "total"]
PottyResult = Literal["satButDry", "wentPotty", "accident"]
ReminderDay = Literal["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


class ReminderIn(StrictModel):
    """Nested inReminder payload used by reminderV2 objects."""

    value: Number
    daytimeOnly: bool
    enabled: bool
    sound: bool
    vibration: bool
    days: list[ReminderDay]


class AtReminderEntry(StrictModel):
    """Entry payload used inside reminderV2.atReminder map."""

    value: Number
    enabled: bool
    sound: bool
    vibration: bool
    days: list[ReminderDay]


class ReminderV2(StrictModel):
    """Reminder payload shape observed in feed/diaper/health prefs."""

    atReminder: dict[str, AtReminderEntry] | None = None
    inReminder: ReminderIn | None = None
    mode: Literal["at", "in"] | None = None


# ---------------------------------------------------------------------------
# users/{uid}
# ---------------------------------------------------------------------------


class FirebaseUserChildRef(StrictModel):
    """Child reference item from users/{uid}.childList."""

    cid: str
    nickname: str | None = None
    picture: str | None = None
    color: str | None = None


class FirebaseHbChildRef(StrictModel):
    """Child reference payload stored under users/{uid}.hbChilds map."""

    addedAt: str


class FirebaseUserSubscriptionData(StrictModel):
    """Subscription payload stored under users/{uid}.subscription."""

    type: Number | None = None
    free_trial_entitlement: str | None = None
    free_trial_plan: str | None = None
    trial_expired_modal: bool | None = None
    free_trial_time: Number | None = None
    expiration: Number | None = None
    free_trial_expiration: Number | None = None


class FirebaseUserDocument(StrictModel):
    """Known fields from users/{uid}."""

    email: str | None = None
    firstname: str | None = None
    lastname: str | None = None
    childList: list[FirebaseUserChildRef]
    lastChild: str | None = None
    # analytics: dict[str, Any] | None = None
    appsFlyerId: str | list[str] | None = None
    childrenUpdatedAt: Number | None = None
    hbChilds: dict[str, FirebaseHbChildRef] | None = None
    installedApps: dict[str, bool] | list[str] | None = None
    isOnboardingCompleted: bool | None = None
    latestTimezone: str | None = None
    # modals: dict[str, Any] | None = None
    onboarding_platform: str | None = None
    showingSwsPredictionModal: bool | None = None
    subscription: FirebaseUserSubscriptionData | None = None
    # summary_reports_visited: Any | None = None
    swsPredictionModalShown: bool | None = None
    tokens: dict[str, str] | None = None
    tooltips: dict[str, bool] | None = None


# ---------------------------------------------------------------------------
# childs/{child_id}
# ---------------------------------------------------------------------------


class FirebaseChildSweetspotStrings(StrictModel):
    """Localized/app-generated SweetSpot display strings."""

    text1: str | None = None
    text2: str | None = None
    text3: str | None = None


class FirebaseChildSweetspot(StrictModel):
    """Known payload for childs/{child_id}.sweetspot."""

    selectedNapDay: Number | None = None
    sweetSpotTimes: list[Number | None] | None = None
    sweetspotStrings: FirebaseChildSweetspotStrings | None = None
    uuid: str | None = None

    @field_validator("sweetSpotTimes", mode="before")
    @classmethod
    def _normalize_sweetspot_times(cls, value: object) -> object:
        """Firebase sends this as either a dict or a list; normalize to list."""
        if isinstance(value, dict):
            if not value:
                return None
            # Determine max index to size the list
            max_idx = max(int(k) for k in value.keys())
            result: list[Number | None] = [None] * (max_idx + 1)
            for k, v in value.items():
                result[int(k)] = v
            return result
        return value


class FirebaseChildDocument(StrictModel):
    """Known fields from childs/{child_id}."""

    # App display-name path reads childs/{cid}.childsName (fallback source is users/{uid}.childList[].nickname).
    childsName: str | None = None
    birthdate: str | Number | None = None
    createdAt: Number | None = None
    gender: GenderType | None = None
    picture: str | None = None
    color: str | None = None
    nightStart: str | Number | None = None
    morningCutoff: str | Number | None = None
    naps: str | None = None
    sweetspot: FirebaseChildSweetspot | None = None
    # analytics: Any | None = None
    pre: Number | None = None
    singleIntervalCount: Number | None = None
    lastInsightRequest: Number | None = None
    categories: dict[str, bool] | None = None
    # celebrations: Any | None = None
    disabledInsights: dict[str, bool] | None = None
    # sleep_scheduler: FirebaseChildSleepScheduler | None = None
    # summaryAssets: Any | None = None
    questionnaireProgress: Number | None = None
    lastQuestionnaireAppVersion: str | None = None
    lastQuestionnaireCompleteTime: Number | None = None


# ---------------------------------------------------------------------------
# types/{child_uid}
# ---------------------------------------------------------------------------


class FirebaseTypesAvailableTypes(StrictModel):
    """Available tracker flags in types/{child_uid}.available_types.

    Includes `solids` when solids type support is enabled.
    """

    solids: bool | None = None


class FirebaseTypesDocument(StrictModel):
    """types/{child_uid} root document.

    `types/{child_uid}/custom/{food_id}` stores user-created solids food types.
    """

    available_types: FirebaseTypesAvailableTypes | None = None


class FirebaseCustomFoodTypeDocument(StrictModel):
    """types/{child_uid}/custom/{food_id} document.

    Custom-food structure used by solids:
    - `id` matches Firestore document id
    - `type` is always "solids"
    - `source` is always "custom"
    - `image` can be empty string or `<id>.jpeg`
    """

    created_at: str
    updated_at: str
    name: str
    archived: bool
    id: str
    type: Literal["solids"]
    image: str
    source: Literal["custom"]


class FirebaseCuratedFoodDocument(StrictModel):
    """Curated solids food entry from Storage object foods/fooddb.json.

    Source is Firebase Storage (bucket `simpleintervals.appspot.com`), not Firestore.
    Payload root is keyed by food id; each value follows this document shape.
    """

    id: str
    name: str
    source: Literal["curated"]
    aka: list[str] | None = None
    is_common_allergen: bool | None = None
    is_high_choking_hazard: bool | None = None
    recommended_age_to_start: Number | None = None
    category: dict[str, bool] | None = None
    link_key: str | None = None
    rank: Number | None = None
    image: str | None = None


# ---------------------------------------------------------------------------
# shared firebase utility payloads
# ---------------------------------------------------------------------------


class FirebaseTimestamp(StrictModel):
    """Firestore timestamp shape used by app documents."""

    seconds: Number
    nanos: int | None = None


# ---------------------------------------------------------------------------
# sleep/{child_uid}
# ---------------------------------------------------------------------------


class FirebaseLastSleepData(StrictModel):
    """sleep/{child_uid}.prefs.lastSleep structure.

    `start` and `duration` are in seconds; `offset` is timezone offset minutes.
    """

    start: Number | None = None
    duration: Number | None = None
    offset: Number | None = None


class FirebaseSleepPrefs(StrictModel):
    """sleep/{child_uid}.prefs structure."""

    lastSleep: FirebaseLastSleepData | None = None
    timestamp: FirebaseTimestamp | None = None
    local_timestamp: Number | None = None
    sweetSpotWhich: Number | None = None
    sweetSpotNotify: Number | None = None


class FirebaseSleepCondition(StrictModel):
    """Sleep condition payload used in sleep timer details."""

    happy: bool | None = None
    longTimeToFallAsleep: bool | None = None
    upset: bool | None = None
    wokeUpChild: bool | None = None
    under_10_minutes: bool | None = None
    ten_to_twenty_minutes: bool | None = Field(default=None, alias="10-20_minutes")


class FirebaseSleepLocations(StrictModel):
    """Sleep location flags."""

    car: bool | None = None
    nursing: bool | None = None
    wornOrHeld: bool | None = None
    stroller: bool | None = None
    coSleep: bool | None = None
    nextToCarer: bool | None = None
    onOwnInBed: bool | None = None
    bottle: bool | None = None
    swing: bool | None = None


class FirebaseSleepDetails(StrictModel):
    """Sleep detail payload."""

    startSleepCondition: FirebaseSleepCondition | None = None
    sleepLocations: FirebaseSleepLocations | None = None
    endSleepCondition: FirebaseSleepCondition | None = None
    notes: str | None = None


class FirebaseSleepSwsDataShown(StrictModel):
    """Known nested payload under sleep timer swsAnalytics.sws_data_shown."""

    nap_number_a: Number | None = None
    nap_number_b: Number | None = None
    prediction_time_a: Number | None = None
    prediction_time_b: Number | None = None
    source_a: str | None = None
    source_b: str | None = None
    wake_window_number_a: Number | None = None
    wake_window_number_b: Number | None = None


class FirebaseSleepSwsAnalytics(StrictModel):
    """Known payload shape observed at sleep/{child_uid}.timer.swsAnalytics."""

    previous_sleep_end_time: Number | None = None
    previous_sleep_interval_key: str | None = None
    sws_data_shown: FirebaseSleepSwsDataShown | None = None
    timestamp: Number | None = None


class FirebaseSleepTimerData(StrictModel):
    """sleep/{child_uid}.timer structure.

    Critical unit rule: `timerStartTime` is milliseconds for sleep timers.
    """

    active: bool
    paused: bool
    timestamp: FirebaseTimestamp | None = None
    local_timestamp: Number | None = None
    timerStartTime: Number | None = Field(
        default=None,
        description="Sleep timer start in milliseconds since epoch.",
    )
    timerEndTime: Number | None = None
    uuid: str
    details: FirebaseSleepDetails | None = None
    swsAnalytics: FirebaseSleepSwsAnalytics | None = None


class FirebaseSleepDocumentData(StrictModel):
    """sleep/{child_uid} root document."""

    timer: FirebaseSleepTimerData | None = None
    prefs: FirebaseSleepPrefs | None = None


class FirebaseSleepIntervalData(StrictModel):
    """sleep/{child_uid}/intervals/{interval_id}.

    Sleep history rows are written to `intervals` subcollection (not root doc).
    """

    id_: str | None = Field(default=None, alias="_id")
    start: Number
    duration: Number
    offset: Number
    end_offset: Number | None = None
    details: FirebaseSleepDetails | None = None
    lastUpdated: Number | None = None


class FirebaseSleepMultiContainer(StrictModel):
    """sleep/{child_uid}/intervals batched multi-entry wrapper."""

    multi: Literal[True]
    hasMoreRoom: bool | None = None
    lastUpdated: Number | None = None
    data: dict[str, FirebaseSleepIntervalData]


# ---------------------------------------------------------------------------
# feed/{child_uid}
# ---------------------------------------------------------------------------


class FirebaseLastNursingData(StrictModel):
    """feed/{child_uid}.prefs.lastNursing structure.

    Aggregated breast-feed summary in seconds (left + right durations).
    """

    mode: Literal["breast"] | None = None
    start: Number | None = None
    duration: Number | None = None
    leftDuration: Number | None = None
    rightDuration: Number | None = None
    offset: Number | None = None


class FirebaseLastSideData(StrictModel):
    """feed/{child_uid}.prefs.lastSide structure."""

    start: Number
    lastSide: FeedSide


class FirebaseLastBottleData(StrictModel):
    """feed/{child_uid}.prefs.lastBottle structure.

    Naming differs from interval rows:
    - prefs use `bottleAmount`/`bottleUnits`
    - intervals use `amount`/`units`
    """

    mode: Literal["bottle"] | None = None
    start: Number | None = None
    bottleType: BottleType | None = None
    bottleAmount: Number | None = None
    bottleUnits: VolumeUnits | None = None
    offset: Number | None = None


class SolidsFoodEntry(StrictModel):
    """Solid food item payload stored under solids interval foods map."""

    id: str
    created_name: str
    source: SolidsFoodSource
    amount: str | Number | None = None


class FirebaseLastSolidData(StrictModel):
    """feed/{child_uid}.prefs.lastSolid structure.

    Mirrors latest solids event summary and powers "last solids" downstream state.
    """

    mode: Literal["solids"] | None = None
    start: Number | None = None
    foods: dict[str, SolidsFoodEntry] | None = None
    reactions: dict[SolidsReaction, bool] | None = None
    notes: str | None = None
    offset: Number | None = None


class FirebaseFeedPrefs(StrictModel):
    """feed/{child_uid}.prefs structure."""

    bottleType: BottleType | None = None
    bottleAmount: Number | None = None
    bottleUnits: VolumeUnits | None = None
    lastBottle: FirebaseLastBottleData | None = None
    lastBottleUuid: str | None = None
    lastNursing: FirebaseLastNursingData | None = None
    lastNursingUuid: str | None = None
    lastSide: FirebaseLastSideData | None = None
    lastSolid: FirebaseLastSolidData | None = None
    reminderV2: ReminderV2 | None = None
    solids_reminderV2: ReminderV2 | None = None
    timestamp: FirebaseTimestamp | None = None
    local_timestamp: Number | None = None


class FirebaseFeedTimerData(StrictModel):
    """feed/{child_uid}.timer structure.

    Timer behavior:
    - `timerStartTime` is seconds (unlike sleep milliseconds)
    - `activeSide` is the current active breast side
    - `timerStartTime` resets on resume/switch-side flows
    - `lastSide` can be `"none"` during transitions
    """

    active: bool
    paused: bool
    timestamp: FirebaseTimestamp | None = None
    local_timestamp: Number | None = None
    feedStartTime: Number | None = Field(
        default=None,
        description="Feeding session start time in seconds since epoch.",
    )
    timerStartTime: Number | None = Field(
        default=None,
        description="Feeding side segment start in seconds; resets on side switch/resume.",
    )
    uuid: str
    leftDuration: Number | None = None
    rightDuration: Number | None = None
    lastSide: FeedSide | None = Field(
        default=None,
        description='Last side marker; may be "none" around transition operations.',
    )
    activeSide: FeedSide | None = Field(
        default=None,
        description="Current active side used by UI for elapsed-time display.",
    )


class FirebaseFeedDocumentData(StrictModel):
    """feed/{child_uid} root document."""

    timer: FirebaseFeedTimerData | None = None
    prefs: FirebaseFeedPrefs | None = None


class FirebaseBreastFeedIntervalData(StrictModel):
    """feed/{child_uid}/intervals breast-mode row.

    Interval ids are typically `{timestamp_ms}-{random_20_chars}`.
    """

    mode: Literal["breast"]
    start: Number
    lastSide: FeedSide
    lastUpdated: Number | None = None
    leftDuration: Number | None = None
    rightDuration: Number | None = None
    offset: Number
    end_offset: Number | None = None
    notes: str | None = None


class FirebaseBottleFeedIntervalData(StrictModel):
    """feed/{child_uid}/intervals bottle-mode row.

    Uses `amount`/`units` field names, unlike prefs.lastBottle naming
    (`bottleAmount`/`bottleUnits`).
    """

    mode: Literal["bottle"]
    start: Number
    lastUpdated: Number | None = None
    bottleType: BottleType
    amount: Number
    units: VolumeUnits
    offset: Number
    end_offset: Number | None = None
    notes: str | None = None


class FirebaseSolidsFeedIntervalData(StrictModel):
    """feed/{child_uid}/intervals solids-mode row.

    Solids history is stored in `feed/{child_uid}/intervals`.
    """

    mode: Literal["solids"]
    start: Number
    lastUpdated: Number | None = None
    offset: Number
    foods: dict[str, SolidsFoodEntry] | None = None
    reactions: dict[SolidsReaction, bool] | None = None
    notes: str | None = None
    foodNoteImage: str | None = None
    multientry_key: str | None = None
    end_offset: Number | None = None


FirebaseFeedIntervalData: TypeAlias = (
    FirebaseBreastFeedIntervalData | FirebaseBottleFeedIntervalData | FirebaseSolidsFeedIntervalData
)


class FirebaseSolidsMultiContainer(StrictModel):
    """Multi-entry wrapper document used by Firestore batch writes."""

    multi: Literal[True]
    hasMoreRoom: bool | None = None
    lastUpdated: Number | None = None
    data: dict[str, FirebaseSolidsFeedIntervalData]


class FirebaseFeedMultiContainer(StrictModel):
    """feed/{child_uid}/intervals batched multi-entry wrapper."""

    multi: Literal[True]
    hasMoreRoom: bool | None = None
    lastUpdated: Number | None = None
    data: dict[str, FirebaseFeedIntervalData]


# ---------------------------------------------------------------------------
# diaper/{child_uid}
# ---------------------------------------------------------------------------


class FirebaseDiaperQuantity(StrictModel):
    """Diaper quantity payload."""

    pee: Number | None = None
    poo: Number | None = None


class FirebaseDiaperData(StrictModel):
    """diaper/{child_uid}/intervals row.

    Diaper entries are instant events (pee/poo/both/dry), not timer sessions.
    """

    mode: DiaperMode
    start: Number
    lastUpdated: Number | None = None
    offset: Number
    quantity: FirebaseDiaperQuantity | None = None
    color: PooColor | None = None
    consistency: PooConsistency | None = None
    diaperRash: bool | None = None
    notes: str | None = None
    isPotty: bool | None = None
    howItHappened: PottyResult | None = None


class FirebaseLastDiaperData(StrictModel):
    """diaper/{child_uid}.prefs.lastDiaper structure."""

    start: Number | None = None
    mode: DiaperMode | None = None
    offset: Number | None = None


class FirebaseLastPottyData(StrictModel):
    """diaper/{child_uid}.prefs.lastPotty structure."""

    mode: DiaperMode | None = None
    start: Number | None = None
    offset: Number | None = None


class FirebaseDiaperPrefs(StrictModel):
    """diaper/{child_uid}.prefs structure."""

    lastDiaper: FirebaseLastDiaperData | None = None
    lastPotty: FirebaseLastPottyData | None = None
    reminderV2: ReminderV2 | None = None
    timestamp: FirebaseTimestamp | None = None
    local_timestamp: Number | None = None


class FirebaseDiaperDocumentData(StrictModel):
    """diaper/{child_uid} root document."""

    prefs: FirebaseDiaperPrefs | None = None


class FirebaseDiaperMultiContainer(StrictModel):
    """diaper/{child_uid}/intervals batched multi-entry wrapper."""

    multi: Literal[True]
    hasMoreRoom: bool | None = None
    lastUpdated: Number | None = None
    data: dict[str, FirebaseDiaperData]


# ---------------------------------------------------------------------------
# health/{child_uid}
# ---------------------------------------------------------------------------


class FirebaseGrowthData(StrictModel):
    """health/{child_uid}/data growth entry payload.

    Health history uses `data` subcollection (not `intervals`).
    """

    id_: str | None = Field(default=None, alias="_id")
    type: Literal["health"] | None = None
    mode: Literal["growth"]
    start: Number
    lastUpdated: Number | None = None
    offset: Number
    isNight: bool | None = None
    multientry_key: str | None = None
    weight: Number | None = None
    weightUnits: WeightUnits | None = None
    height: Number | None = None
    heightUnits: HeightUnits | None = None
    head: Number | None = None
    headUnits: HeadUnits | None = None


class FirebaseMedicationData(StrictModel):
    """health/{child_uid}/data medication entry payload.

    Health tracker writes medication rows to `health/{child_uid}/data`.
    """

    type: Literal["health"] | None = None
    mode: Literal["medication"]
    start: Number
    lastUpdated: Number | None = None
    offset: Number
    medication_id: str | None = None
    medication_name: str | None = None
    amount: Number | None = None
    units: MedicationUnits | None = None
    notes: str | None = None
    multientry_key: str | None = None


class FirebaseTemperatureData(StrictModel):
    """health/{child_uid}/data temperature entry payload.

    Health tracker writes temperature rows to `health/{child_uid}/data`.
    """

    type: Literal["health"] | None = None
    mode: Literal["temperature"]
    start: Number
    lastUpdated: Number | None = None
    offset: Number
    amount: Number | None = None
    units: TemperatureUnits | None = None
    multientry_key: str | None = None


HealthDataEntry: TypeAlias = FirebaseGrowthData | FirebaseMedicationData | FirebaseTemperatureData


class FirebaseHealthPrefs(StrictModel):
    """health/{child_uid}.prefs structure."""

    lastGrowthEntry: FirebaseGrowthData | None = None
    lastMedication: FirebaseMedicationData | None = None
    lastTemperature: FirebaseTemperatureData | None = None
    reminderV2: ReminderV2 | None = None
    timestamp: FirebaseTimestamp | None = None
    local_timestamp: Number | None = None


class FirebaseHealthDocumentData(StrictModel):
    """health/{child_uid} root document."""

    prefs: FirebaseHealthPrefs | None = None


class FirebaseHealthMultiContainer(StrictModel):
    """health/{child_uid}/data batched multi-entry wrapper."""

    multi: Literal[True]
    hasMoreRoom: bool | None = None
    lastUpdated: Number | None = None
    data: dict[str, HealthDataEntry]


# ---------------------------------------------------------------------------
# pump/{child_uid}
# ---------------------------------------------------------------------------


class FirebaseLastPumpData(StrictModel):
    """pump/{child_uid}.prefs.lastPump structure.

    Live Firebase shows `entryMode`; for `total` entries the app stores half of the
    total in each side field so leftAmount + rightAmount equals the entered total.
    """

    start: Number | None = None
    duration: Number | None = None
    entryMode: PumpEntryMode | None = None
    leftAmount: Number | None = None
    rightAmount: Number | None = None
    units: VolumeUnits | None = None
    offset: Number | None = None


class FirebasePumpPrefs(StrictModel):
    """pump/{child_uid}.prefs structure."""

    lastPump: FirebaseLastPumpData | None = None
    reminderV2: ReminderV2 | None = None
    timestamp: FirebaseTimestamp | None = None
    local_timestamp: Number | None = None


class FirebasePumpTimerData(StrictModel):
    """pump/{child_uid}.timer structure."""

    active: bool
    paused: bool | None = None
    timestamp: FirebaseTimestamp | None = None
    local_timestamp: Number | None = None
    startTime: Number | None = Field(
        default=None,
        description="Observed pump timer field in milliseconds since epoch.",
    )
    entryMode: PumpEntryMode | None = None
    units: VolumeUnits | None = None
    notes: str | None = None
    uuid: str


class FirebasePumpDocumentData(StrictModel):
    """pump/{child_uid} root document."""

    timer: FirebasePumpTimerData | None = None
    prefs: FirebasePumpPrefs | None = None


class FirebasePumpIntervalData(StrictModel):
    """pump/{child_uid}/intervals row.

    Pump tracker follows the common `intervals` subcollection convention.
    """

    start: Number
    entryMode: PumpEntryMode
    leftAmount: Number | None = None
    rightAmount: Number | None = None
    units: VolumeUnits
    offset: Number
    duration: Number | None = None
    end_offset: Number | None = None
    lastUpdated: Number | None = None
    notes: str | None = None


class FirebasePumpMultiContainer(StrictModel):
    """pump/{child_uid}/intervals batched multi-entry wrapper."""

    multi: Literal[True]
    hasMoreRoom: bool | None = None
    lastUpdated: Number | None = None
    data: dict[str, FirebasePumpIntervalData]


# ---------------------------------------------------------------------------
# activities/{child_uid}
# ---------------------------------------------------------------------------


class FirebaseLastActivityData(StrictModel):
    """activities/{child_uid}.prefs.last* structure.

    One summary field per activity mode under `prefs`, for
    example `lastBath` and `lastStoryTime`.
    """

    start: Number | None = None
    offset: Number | None = None
    duration: Number | None = None
    end_offset: Number | None = None


class FirebaseActivityPrefs(StrictModel):
    """activities/{child_uid}.prefs structure."""

    lastBath: FirebaseLastActivityData | None = None
    lastBrushTeeth: FirebaseLastActivityData | None = None
    lastIndoorPlay: FirebaseLastActivityData | None = None
    lastOutdoorPlay: FirebaseLastActivityData | None = None
    lastScreenTime: FirebaseLastActivityData | None = None
    lastSkinToSkin: FirebaseLastActivityData | None = None
    lastStoryTime: FirebaseLastActivityData | None = None
    lastTummyTime: FirebaseLastActivityData | None = None
    timestamp: FirebaseTimestamp | None = None
    local_timestamp: Number | None = None


class FirebaseActivityTimerEntryData(StrictModel):
    """Per-mode timer entry from activities/{child_uid}.timer.<mode>."""

    active: bool
    paused: bool | None = None
    timestamp: FirebaseTimestamp | None = None
    local_timestamp: Number | None = None
    startTime: Number | None = Field(
        default=None,
        description="Observed activity timer field in milliseconds since epoch.",
    )
    endTime: Number | None = Field(
        default=None,
        description="Observed on live bath timer payloads in milliseconds since epoch.",
    )
    duration: Number | None = None
    notes: str | None = None
    uuid: str


class FirebaseActivityTimerData(StrictModel):
    """activities/{child_uid}.timer structure keyed by activity mode."""

    bath: FirebaseActivityTimerEntryData | None = None
    brushTeeth: FirebaseActivityTimerEntryData | None = None
    indoorPlay: FirebaseActivityTimerEntryData | None = None
    outdoorPlay: FirebaseActivityTimerEntryData | None = None
    screenTime: FirebaseActivityTimerEntryData | None = None
    skinToSkin: FirebaseActivityTimerEntryData | None = None
    storyTime: FirebaseActivityTimerEntryData | None = None
    tummyTime: FirebaseActivityTimerEntryData | None = None


class FirebaseActivityDocumentData(StrictModel):
    """activities/{child_uid} root document."""

    timer: FirebaseActivityTimerData | None = None
    prefs: FirebaseActivityPrefs | None = None


class FirebaseActivityIntervalData(StrictModel):
    """activities/{child_uid}/intervals row.

    Activities tracker follows the common `intervals` subcollection convention.
    """

    mode: ActivityMode
    start: Number
    offset: Number
    duration: Number | None = None
    end_offset: Number | None = None
    lastUpdated: Number | None = None
    notes: str | None = None


class FirebaseActivityMultiContainer(StrictModel):
    """activities/{child_uid}/intervals batched multi-entry wrapper."""

    multi: Literal[True]
    hasMoreRoom: bool | None = None
    lastUpdated: Number | None = None
    data: dict[str, FirebaseActivityIntervalData]


def to_firebase_dict(model: StrictModel) -> dict[str, object]:
    """Serialize strict model to Firestore payload."""

    return model.model_dump(by_alias=True, exclude_none=True)
