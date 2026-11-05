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

from .account import AccountMixin
from .activity import ActivityMixin
from .client import FirebaseTokenCredentials as FirebaseTokenCredentials
from .const import CURATED_FOODS_BUCKET as CURATED_FOODS_BUCKET
from .const import CURATED_FOODS_OBJECT as CURATED_FOODS_OBJECT
from .diaper import DiaperMixin
from .feeding import FeedingMixin
from .health import HealthMixin
from .history import HistoryMixin
from .listeners import ListenerMixin
from .listeners import TDocumentData as TDocumentData
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
