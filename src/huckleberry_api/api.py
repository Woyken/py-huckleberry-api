"""Huckleberry API client.

The public interface is :class:`HuckleberryAPI`. Its methods are assembled from one
mixin per feature so each tracker can be read and changed in isolation:

    account.py    child and user profile reads
    sleep.py      sleep timer and sleep history
    feeding.py    nursing timer, bottle feeds, nursing history
    solids.py     solids catalog, custom foods, logged meals
    diaper.py     diaper and potty events
    health.py     growth, temperature, medicine
    pump.py       pump timer and pump history
    activity.py   activity timers and logs
    history.py    date-range queries over saved history
    listeners.py  real-time snapshot listeners
    client.py     authentication, Firestore clients, timezone plumbing

Shared constants live in ``const.py`` and shared internals in ``common.py``. Firebase
payload schemas live in ``firebase_types.py``; API input models in ``models.py``.
"""

from __future__ import annotations

import time as time

from .account import AccountMixin
from .activity import ActivityMixin
from .client import FirebaseTokenCredentials as FirebaseTokenCredentials
from .const import CURATED_FOODS_BUCKET as CURATED_FOODS_BUCKET
from .const import CURATED_FOODS_OBJECT as CURATED_FOODS_OBJECT
from .const import AUTH_URL as AUTH_URL
from .const import FIREBASE_API_KEY as FIREBASE_API_KEY
from .const import REFRESH_URL as REFRESH_URL
from .diaper import DiaperMixin
from .feeding import FeedingMixin

# Backward-compatible re-exports. These names were importable from
# ``huckleberry_api.api`` before the split into feature modules; new code should
# import them from the module that defines them.
from .firebase_types import ActivityMode as ActivityMode
from .firebase_types import BottleType as BottleType
from .firebase_types import DiaperMode as DiaperMode
from .firebase_types import FeedSide as FeedSide
from .firebase_types import FirebaseActivityDocumentData as FirebaseActivityDocumentData
from .firebase_types import FirebaseActivityIntervalData as FirebaseActivityIntervalData
from .firebase_types import FirebaseActivityMultiContainer as FirebaseActivityMultiContainer
from .firebase_types import FirebaseActivityTimerEntryData as FirebaseActivityTimerEntryData
from .firebase_types import FirebaseBottleFeedIntervalData as FirebaseBottleFeedIntervalData
from .firebase_types import FirebaseBreastFeedIntervalData as FirebaseBreastFeedIntervalData
from .firebase_types import FirebaseChildDocument as FirebaseChildDocument
from .firebase_types import FirebaseCuratedFoodDocument as FirebaseCuratedFoodDocument
from .firebase_types import FirebaseCustomFoodTypeDocument as FirebaseCustomFoodTypeDocument
from .firebase_types import FirebaseDiaperData as FirebaseDiaperData
from .firebase_types import FirebaseDiaperDocumentData as FirebaseDiaperDocumentData
from .firebase_types import FirebaseDiaperMultiContainer as FirebaseDiaperMultiContainer
from .firebase_types import FirebaseDiaperQuantity as FirebaseDiaperQuantity
from .firebase_types import FirebaseFeedDocumentData as FirebaseFeedDocumentData
from .firebase_types import FirebaseFeedIntervalData as FirebaseFeedIntervalData
from .firebase_types import FirebaseFeedMultiContainer as FirebaseFeedMultiContainer
from .firebase_types import FirebaseFeedTimerData as FirebaseFeedTimerData
from .firebase_types import FirebaseGrowthData as FirebaseGrowthData
from .firebase_types import FirebaseHealthDocumentData as FirebaseHealthDocumentData
from .firebase_types import FirebaseHealthMultiContainer as FirebaseHealthMultiContainer
from .firebase_types import FirebaseLastActivityData as FirebaseLastActivityData
from .firebase_types import FirebaseLastBottleData as FirebaseLastBottleData
from .firebase_types import FirebaseLastDiaperData as FirebaseLastDiaperData
from .firebase_types import FirebaseLastGrowthData as FirebaseLastGrowthData
from .firebase_types import FirebaseLastMedicationData as FirebaseLastMedicationData
from .firebase_types import FirebaseLastNursingData as FirebaseLastNursingData
from .firebase_types import FirebaseLastPottyData as FirebaseLastPottyData
from .firebase_types import FirebaseLastPumpData as FirebaseLastPumpData
from .firebase_types import FirebaseLastSideData as FirebaseLastSideData
from .firebase_types import FirebaseLastSleepData as FirebaseLastSleepData
from .firebase_types import FirebaseLastSolidData as FirebaseLastSolidData
from .firebase_types import FirebaseLastTemperatureData as FirebaseLastTemperatureData
from .firebase_types import FirebaseMedicationData as FirebaseMedicationData
from .firebase_types import FirebaseMedicationTypeDocument as FirebaseMedicationTypeDocument
from .firebase_types import FirebaseMedicationTypeLastTake as FirebaseMedicationTypeLastTake
from .firebase_types import FirebasePumpDocumentData as FirebasePumpDocumentData
from .firebase_types import FirebasePumpIntervalData as FirebasePumpIntervalData
from .firebase_types import FirebasePumpMultiContainer as FirebasePumpMultiContainer
from .firebase_types import FirebasePumpTimerData as FirebasePumpTimerData
from .firebase_types import FirebaseSleepCondition as FirebaseSleepCondition
from .firebase_types import FirebaseSleepDetails as FirebaseSleepDetails
from .firebase_types import FirebaseSleepDocumentData as FirebaseSleepDocumentData
from .firebase_types import FirebaseSleepIntervalData as FirebaseSleepIntervalData
from .firebase_types import FirebaseSleepLocations as FirebaseSleepLocations
from .firebase_types import FirebaseSleepMultiContainer as FirebaseSleepMultiContainer
from .firebase_types import FirebaseSleepTimerData as FirebaseSleepTimerData
from .firebase_types import FirebaseSolidsFeedIntervalData as FirebaseSolidsFeedIntervalData
from .firebase_types import FirebaseTemperatureData as FirebaseTemperatureData
from .firebase_types import FirebaseTimestamp as FirebaseTimestamp
from .firebase_types import FirebaseTypesAvailableTypes as FirebaseTypesAvailableTypes
from .firebase_types import FirebaseTypesDocument as FirebaseTypesDocument
from .firebase_types import FirebaseUserDocument as FirebaseUserDocument
from .firebase_types import HealthDataEntry as HealthDataEntry
from .firebase_types import MedicationUnits as MedicationUnits
from .firebase_types import PooColor as PooColor
from .firebase_types import PooConsistency as PooConsistency
from .firebase_types import PottyResult as PottyResult
from .firebase_types import PumpEntryMode as PumpEntryMode
from .firebase_types import SolidsFoodEntry as SolidsFoodEntry
from .firebase_types import SolidsReaction as SolidsReaction
from .firebase_types import TemperatureUnits as TemperatureUnits
from .firebase_types import VolumeUnits as VolumeUnits
from .firebase_types import to_firebase_dict as to_firebase_dict
from .health import HealthMixin
from .history import HistoryMixin
from .listeners import ListenerMixin
from .listeners import TDocumentData as TDocumentData
from .models import SolidsFoodReference as SolidsFoodReference
from .pump import PumpMixin
from .sleep import SleepMixin
from .solids import SolidsMixin


class HuckleberryAPI(
    AccountMixin,
    SleepMixin,
    FeedingMixin,
    SolidsMixin,
    DiaperMixin,
    HealthMixin,
    PumpMixin,
    ActivityMixin,
    ListenerMixin,
    HistoryMixin,
):
    """API client for Huckleberry.

    Each feature is implemented in its own module; see the package docstring above
    for the module-to-feature map.
    """
