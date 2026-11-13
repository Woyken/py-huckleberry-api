"""Huckleberry API client.

The public interface is :class:`HuckleberryAPI`. Its methods are assembled from one
mixin per feature so each tracker can be read and changed in isolation:

    _account.py    child and user profile reads
    _sleep.py      sleep timer and sleep history
    _feeding.py    nursing timer, bottle feeds, nursing history
    _solids.py     solids catalog, custom foods, logged meals
    _diaper.py     diaper and potty events
    _health.py     growth, temperature, medicine
    _pump.py       pump timer and pump history
    _activity.py   activity timers and logs
    _history.py    date-range queries over saved history
    _listeners.py  real-time snapshot listeners
    _client.py     authentication, Firestore clients, timezone plumbing

Modules prefixed with an underscore are internal. Shared constants live in
``_const.py`` and shared internals in ``_common.py``. Firebase payload schemas live in
``firebase_types.py``; API input models in ``models.py``.
"""

from __future__ import annotations

from ._account import AccountMixin
from ._activity import ActivityMixin
from ._diaper import DiaperMixin
from ._feeding import FeedingMixin
from ._health import HealthMixin
from ._history import HistoryMixin
from ._listeners import ListenerMixin
from ._pump import PumpMixin
from ._sleep import SleepMixin
from ._solids import SolidsMixin

__all__ = ["HuckleberryAPI"]


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
