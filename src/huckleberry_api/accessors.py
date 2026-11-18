"""Child-scoped accessors: the preferred public shape.

Every document this library touches hangs off a child, so a child handle binds
``child_uid`` once and groups the trackers under it::

    child = api.child(child_uid)
    async with child.sleep.watch() as sub:
        async for snap in sub: ...

    await child.sleep.start()
    rows = await child.sleep.history(start, end)

Handles are stateless value objects: they hold the API reference and the child
uid, never child data, so one can be created per call or kept for the lifetime
of a request without cache-invalidation concerns.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from .firebase_types import ActivityMode
from .firebase_types import BottleType
from .firebase_types import DiaperAmount
from .firebase_types import DiaperMode
from .firebase_types import FeedSide
from .firebase_types import FirebaseActivityDocumentData
from .firebase_types import FirebaseActivityIntervalData
from .firebase_types import FirebaseChildDocument
from .firebase_types import FirebaseCuratedFoodDocument
from .firebase_types import FirebaseCustomFoodTypeDocument
from .firebase_types import FirebaseDiaperData
from .firebase_types import FirebaseDiaperDocumentData
from .firebase_types import FirebaseFeedDocumentData
from .firebase_types import FirebaseFeedIntervalData
from .firebase_types import FirebaseGrowthData
from .firebase_types import FirebaseHealthDocumentData
from .firebase_types import FirebaseMedicationTypeDocument
from .firebase_types import FirebasePumpDocumentData
from .firebase_types import FirebasePumpIntervalData
from .firebase_types import FirebaseSleepDetails
from .firebase_types import FirebaseSleepDocumentData
from .firebase_types import FirebaseSleepIntervalData
from .firebase_types import HealthDataEntry
from .firebase_types import MedicationUnits
from .firebase_types import PooColor
from .firebase_types import PooConsistency
from .firebase_types import PottyResult
from .firebase_types import SolidsReaction
from .firebase_types import TemperatureUnits
from .firebase_types import VolumeUnits
from .models import SolidsFoodReference
from .subscriptions import Subscription

if TYPE_CHECKING:
    from .api import HuckleberryAPI

__all__ = [
    "ActivityHandle",
    "BottleHandle",
    "ChildHandle",
    "DiaperHandle",
    "FeedingHandle",
    "HealthHandle",
    "NursingHandle",
    "PumpHandle",
    "SleepHandle",
    "SolidsHandle",
]


class _FeatureHandle:
    """Base for stateless per-child tracker handles."""

    __slots__ = ("_api", "_uid")

    def __init__(self, api: "HuckleberryAPI", child_uid: str) -> None:
        self._api = api
        self._uid = child_uid

    @property
    def child_uid(self) -> str:
        """The child this handle is bound to."""
        return self._uid


class SleepHandle(_FeatureHandle):
    """Sleep tracker for one child."""

    __slots__ = ()

    def watch(self) -> Subscription[FirebaseSleepDocumentData]:
        return self._api.watch_sleep(self._uid)

    async def state(self) -> FirebaseSleepDocumentData | None:
        return await self._api.get_sleep(self._uid)

    async def start(self, *, start_time: datetime | None = None) -> None:
        await self._api.start_sleep(self._uid, start_time=start_time)

    async def pause(self) -> None:
        await self._api.pause_sleep(self._uid)

    async def resume(self) -> None:
        await self._api.resume_sleep(self._uid)

    async def cancel(self) -> None:
        await self._api.cancel_sleep(self._uid)

    async def complete(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        details: FirebaseSleepDetails | None = None,
    ) -> None:
        await self._api.complete_sleep(self._uid, start_time=start_time, end_time=end_time, details=details)

    async def log(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        details: FirebaseSleepDetails | None = None,
    ) -> None:
        await self._api.log_sleep(self._uid, start_time=start_time, end_time=end_time, details=details)

    async def history(self, start_time: datetime, end_time: datetime) -> list[FirebaseSleepIntervalData]:
        return await self._api.list_sleep_intervals(self._uid, start_time, end_time)


class NursingHandle(_FeatureHandle):
    """Nursing timer and history for one child."""

    __slots__ = ()

    async def start(self, side: FeedSide = "left", *, start_time: datetime | None = None) -> None:
        await self._api.start_nursing(self._uid, side, start_time=start_time)

    async def pause(self) -> None:
        await self._api.pause_nursing(self._uid)

    async def resume(self, side: FeedSide | None = None) -> None:
        await self._api.resume_nursing(self._uid, side)

    async def switch_side(self) -> None:
        await self._api.switch_nursing_side(self._uid)

    async def cancel(self) -> None:
        await self._api.cancel_nursing(self._uid)

    async def complete(self, *, start_time: datetime | None = None, end_time: datetime | None = None) -> None:
        await self._api.complete_nursing(self._uid, start_time=start_time, end_time=end_time)

    async def log(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        side: FeedSide = "left",
        left_duration: float | int | None = None,
        right_duration: float | int | None = None,
        notes: str | None = None,
    ) -> None:
        await self._api.log_nursing(
            self._uid,
            start_time=start_time,
            end_time=end_time,
            side=side,
            left_duration=left_duration,
            right_duration=right_duration,
            notes=notes,
        )


class BottleHandle(_FeatureHandle):
    """Bottle feeds for one child."""

    __slots__ = ()

    async def log(
        self,
        *,
        start_time: datetime,
        amount: float,
        bottle_type: BottleType = "Formula",
        units: VolumeUnits = "ml",
        notes: str | None = None,
    ) -> None:
        await self._api.log_bottle(
            self._uid,
            start_time=start_time,
            amount=amount,
            bottle_type=bottle_type,
            units=units,
            notes=notes,
        )


class SolidsHandle(_FeatureHandle):
    """Solids: catalog lookups and logged meals for one child."""

    __slots__ = ()

    async def curated_foods(self) -> list[FirebaseCuratedFoodDocument]:
        """Account-wide curated catalog. Independent of the child."""
        return await self._api.list_solids_curated_foods()

    async def custom_foods(self, *, include_archived: bool = False) -> list[FirebaseCustomFoodTypeDocument]:
        return await self._api.list_solids_custom_foods(self._uid, include_archived)

    async def create_custom_food(self, name: str, image: str = "") -> FirebaseCustomFoodTypeDocument:
        return await self._api.create_solids_custom_food(self._uid, name, image)

    async def log(
        self,
        *,
        start_time: datetime,
        foods: list[SolidsFoodReference],
        notes: str = "",
        reaction: SolidsReaction | None = None,
        food_note_image: str | None = None,
    ) -> None:
        await self._api.log_solids(
            self._uid,
            start_time=start_time,
            foods=foods,
            notes=notes,
            reaction=reaction,
            food_note_image=food_note_image,
        )


class FeedingHandle(_FeatureHandle):
    """Feeding document: live timer plus nursing, bottle and solids handles."""

    __slots__ = ("bottle", "nursing", "solids")

    def __init__(self, api: "HuckleberryAPI", child_uid: str) -> None:
        super().__init__(api, child_uid)
        self.nursing = NursingHandle(api, child_uid)
        self.bottle = BottleHandle(api, child_uid)
        self.solids = SolidsHandle(api, child_uid)

    def watch(self) -> Subscription[FirebaseFeedDocumentData]:
        return self._api.watch_feed(self._uid)

    async def state(self) -> FirebaseFeedDocumentData | None:
        """The live feeding document, including the nursing timer."""
        return await self._api.get_nursing(self._uid)

    async def history(self, start_time: datetime, end_time: datetime) -> list[FirebaseFeedIntervalData]:
        return await self._api.list_feed_intervals(self._uid, start_time, end_time)


class PumpHandle(_FeatureHandle):
    """Pumping timer and history for one child."""

    __slots__ = ()

    def watch(self) -> Subscription[FirebasePumpDocumentData]:
        return self._api.watch_pump(self._uid)

    async def start(self, *, start_time: datetime | None = None) -> None:
        await self._api.start_pump(self._uid, start_time=start_time)

    async def pause(self) -> None:
        await self._api.pause_pump(self._uid)

    async def resume(self) -> None:
        await self._api.resume_pump(self._uid)

    async def cancel(self) -> None:
        await self._api.cancel_pump(self._uid)

    async def complete(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        left_amount: float | int | None = None,
        right_amount: float | int | None = None,
        total_amount: float | int | None = None,
        units: VolumeUnits | None = None,
        notes: str | None = None,
    ) -> None:
        await self._api.complete_pump(
            self._uid,
            start_time=start_time,
            end_time=end_time,
            left_amount=left_amount,
            right_amount=right_amount,
            total_amount=total_amount,
            units=units,
            notes=notes,
        )

    async def log(
        self,
        *,
        start_time: datetime,
        duration: float | int | None = None,
        left_amount: float | int | None = None,
        right_amount: float | int | None = None,
        total_amount: float | int | None = None,
        units: VolumeUnits = "ml",
        notes: str | None = None,
    ) -> None:
        await self._api.log_pump(
            self._uid,
            start_time=start_time,
            duration=duration,
            left_amount=left_amount,
            right_amount=right_amount,
            total_amount=total_amount,
            units=units,
            notes=notes,
        )

    async def history(self, start_time: datetime, end_time: datetime) -> list[FirebasePumpIntervalData]:
        return await self._api.list_pump_intervals(self._uid, start_time, end_time)


class ActivityHandle(_FeatureHandle):
    """Activity timers and logs for one child."""

    __slots__ = ()

    def watch(self) -> Subscription[FirebaseActivityDocumentData]:
        return self._api.watch_activity(self._uid)

    async def start(self, mode: ActivityMode, *, start_time: datetime | None = None) -> None:
        await self._api.start_activity(self._uid, mode, start_time=start_time)

    async def pause(self, mode: ActivityMode) -> None:
        await self._api.pause_activity(self._uid, mode)

    async def resume(self, mode: ActivityMode) -> None:
        await self._api.resume_activity(self._uid, mode)

    async def cancel(self, mode: ActivityMode) -> None:
        await self._api.cancel_activity(self._uid, mode)

    async def switch(self, mode: ActivityMode) -> None:
        await self._api.switch_activity(self._uid, mode)

    async def complete(
        self,
        mode: ActivityMode,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        duration: float | int | None = None,
        notes: str | None = None,
    ) -> None:
        await self._api.complete_activity(
            self._uid,
            mode,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            notes=notes,
        )

    async def log(
        self,
        *,
        mode: ActivityMode,
        start_time: datetime,
        duration: float | int | None = None,
        notes: str | None = None,
    ) -> None:
        await self._api.log_activity(self._uid, mode=mode, start_time=start_time, duration=duration, notes=notes)

    async def history(self, start_time: datetime, end_time: datetime) -> list[FirebaseActivityIntervalData]:
        return await self._api.list_activity_intervals(self._uid, start_time, end_time)


class DiaperHandle(_FeatureHandle):
    """Diaper and potty events for one child."""

    __slots__ = ()

    def watch(self) -> Subscription[FirebaseDiaperDocumentData]:
        return self._api.watch_diaper(self._uid)

    async def log(
        self,
        *,
        start_time: datetime,
        mode: DiaperMode,
        pee_amount: DiaperAmount | None = None,
        poo_amount: DiaperAmount | None = None,
        color: PooColor | None = None,
        consistency: PooConsistency | None = None,
        diaper_rash: bool = False,
        notes: str | None = None,
    ) -> None:
        await self._api.log_diaper(
            self._uid,
            start_time=start_time,
            mode=mode,
            pee_amount=pee_amount,
            poo_amount=poo_amount,
            color=color,
            consistency=consistency,
            diaper_rash=diaper_rash,
            notes=notes,
        )

    async def log_potty(
        self,
        *,
        start_time: datetime,
        mode: DiaperMode,
        how_it_happened: PottyResult,
        pee_amount: DiaperAmount | None = None,
        poo_amount: DiaperAmount | None = None,
        color: PooColor | None = None,
        consistency: PooConsistency | None = None,
        notes: str | None = None,
    ) -> None:
        await self._api.log_potty(
            self._uid,
            start_time=start_time,
            mode=mode,
            how_it_happened=how_it_happened,
            pee_amount=pee_amount,
            poo_amount=poo_amount,
            color=color,
            consistency=consistency,
            notes=notes,
        )

    async def history(self, start_time: datetime, end_time: datetime) -> list[FirebaseDiaperData]:
        return await self._api.list_diaper_intervals(self._uid, start_time, end_time)


class HealthHandle(_FeatureHandle):
    """Growth, temperature and medicine for one child."""

    __slots__ = ()

    def watch(self) -> Subscription[FirebaseHealthDocumentData]:
        return self._api.watch_health(self._uid)

    async def log_growth(
        self,
        *,
        start_time: datetime,
        weight: float | None = None,
        height: float | None = None,
        head: float | None = None,
        units: Literal["metric", "imperial"] = "metric",
    ) -> None:
        await self._api.log_growth(
            self._uid, start_time=start_time, weight=weight, height=height, head=head, units=units
        )

    async def log_temperature(
        self,
        *,
        start_time: datetime,
        amount: float,
        units: TemperatureUnits,
        notes: str | None = None,
    ) -> None:
        await self._api.log_temperature(self._uid, start_time=start_time, amount=amount, units=units, notes=notes)

    async def latest_growth(self) -> FirebaseGrowthData | None:
        return await self._api.get_latest_growth(self._uid)

    async def medicine_types(self) -> list[FirebaseMedicationTypeDocument]:
        return await self._api.list_medicine_types(self._uid)

    async def create_medicine_type(self, name: str) -> FirebaseMedicationTypeDocument:
        return await self._api.create_medicine_type(self._uid, name)

    async def log_medicine(
        self,
        *,
        start_time: datetime,
        medicine_type: FirebaseMedicationTypeDocument,
        amount: float | None = None,
        units: MedicationUnits | None = None,
        notes: str = "",
    ) -> None:
        await self._api.log_medicine(
            self._uid,
            start_time=start_time,
            medicine_type=medicine_type,
            amount=amount,
            units=units,
            notes=notes,
        )

    async def history(self, start_time: datetime, end_time: datetime) -> list[HealthDataEntry]:
        return await self._api.list_health_entries(self._uid, start_time, end_time)


class ChildHandle:
    """A child, with one stateless handle per tracker.

    Create it with :meth:`HuckleberryAPI.child`.  It performs no I/O and holds
    no child data: it only binds ``child_uid`` and the API reference.
    """

    __slots__ = ("_api", "_uid", "activity", "diaper", "feeding", "health", "pump", "sleep")

    def __init__(self, api: "HuckleberryAPI", child_uid: str) -> None:
        self._api = api
        self._uid = child_uid
        self.sleep = SleepHandle(api, child_uid)
        self.feeding = FeedingHandle(api, child_uid)
        self.pump = PumpHandle(api, child_uid)
        self.activity = ActivityHandle(api, child_uid)
        self.diaper = DiaperHandle(api, child_uid)
        self.health = HealthHandle(api, child_uid)

    @property
    def child_uid(self) -> str:
        """The child this handle is bound to."""
        return self._uid

    def watch(self) -> Subscription[FirebaseChildDocument]:
        """Watch the ``childs/{uid}`` profile document."""
        return self._api.watch_child(self._uid)

    async def profile(self) -> FirebaseChildDocument | None:
        return await self._api.get_child(self._uid)
