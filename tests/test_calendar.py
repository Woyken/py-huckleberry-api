"""Calendar/interval fetching tests for Huckleberry API."""

import asyncio
from datetime import datetime, timedelta, timezone

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import (
    FirebaseActivityIntervalData,
    FirebaseBottleFeedIntervalData,
    FirebaseBreastFeedIntervalData,
    FirebaseGrowthData,
    FirebaseMedicationData,
    FirebasePumpIntervalData,
    FirebaseSolidsFeedIntervalData,
)


class TestCalendarIntervals:
    """Test calendar interval fetching functionality."""

    async def test_list_sleep_intervals(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test fetching sleep intervals for a date range."""
        # Create a sleep interval first
        await api.start_sleep(child_uid)
        await asyncio.sleep(2)
        await api.complete_sleep(child_uid)
        await asyncio.sleep(1)

        # Query for intervals in the last hour
        now = datetime.now(timezone.utc)
        start_time = now.replace(microsecond=0) - timedelta(hours=1)
        end_time = now.replace(microsecond=0) + timedelta(minutes=1)

        intervals = await api.list_sleep_intervals(child_uid, start_time, end_time)

        assert isinstance(intervals, list)
        # Should have at least the interval we just created
        assert len(intervals) >= 1

        # Check structure
        for interval in intervals:
            assert isinstance(interval.start, (int, float))
            assert isinstance(interval.duration, (int, float))

    async def test_list_feed_intervals(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test fetching feed intervals for a date range."""
        # Create a feed interval first
        await api.start_nursing(child_uid, side="left")
        await asyncio.sleep(2)
        await api.complete_nursing(child_uid)
        await asyncio.sleep(1)

        # Query for intervals in the last hour
        now = datetime.now(timezone.utc)
        start_time = now.replace(microsecond=0) - timedelta(hours=1)
        end_time = now.replace(microsecond=0) + timedelta(minutes=1)

        intervals = await api.list_feed_intervals(child_uid, start_time, end_time)

        assert isinstance(intervals, list)
        assert len(intervals) >= 1

        # Check structure
        for interval in intervals:
            assert isinstance(interval.start, (int, float))
            if isinstance(interval, FirebaseBreastFeedIntervalData):
                assert interval.leftDuration is None or isinstance(interval.leftDuration, (int, float))
                assert interval.rightDuration is None or isinstance(interval.rightDuration, (int, float))
            elif isinstance(interval, FirebaseBottleFeedIntervalData):
                # The app can save a bottle feed without a volume.
                assert interval.amount is None or isinstance(interval.amount, (int, float))
            elif isinstance(interval, FirebaseSolidsFeedIntervalData):
                assert interval.foods is None or isinstance(interval.foods, dict)

    async def test_list_diaper_intervals(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test fetching diaper intervals for a date range."""
        # Create a diaper entry first
        await api.log_diaper(child_uid, start_time=datetime.now(timezone.utc).replace(microsecond=0), mode="pee")
        await asyncio.sleep(1)

        # Query for intervals in the last hour
        now = datetime.now(timezone.utc)
        start_time = now.replace(microsecond=0) - timedelta(hours=1)
        end_time = now.replace(microsecond=0) + timedelta(minutes=1)

        intervals = await api.list_diaper_intervals(child_uid, start_time, end_time)

        assert isinstance(intervals, list)
        assert len(intervals) >= 1

        # Check structure
        for interval in intervals:
            assert isinstance(interval.start, (int, float))
            assert interval.mode in ("pee", "poo", "both", "dry")

    async def test_list_health_entries(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test fetching health/growth entries for a date range."""
        # Create a health entry first
        await api.log_growth(
            child_uid,
            start_time=datetime.now(timezone.utc).replace(microsecond=0),
            weight=5.0,
            units="metric",
        )
        await asyncio.sleep(1)

        # Query for entries in the last hour
        now = datetime.now(timezone.utc)
        start_time = now.replace(microsecond=0) - timedelta(hours=1)
        end_time = now.replace(microsecond=0) + timedelta(minutes=1)

        entries = await api.list_health_entries(child_uid, start_time, end_time)

        assert isinstance(entries, list)
        assert len(entries) >= 1

        # Check structure
        for entry in entries:
            assert isinstance(entry.start, (int, float))
            if isinstance(entry, FirebaseGrowthData):
                has_measurement = entry.weight is not None or entry.height is not None or entry.head is not None
                assert has_measurement
            elif isinstance(entry, FirebaseMedicationData):
                assert entry.medication_name is not None or entry.amount is not None

    async def test_list_pump_intervals(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test fetching pump intervals for a date range."""
        await api.log_pump(
            child_uid,
            start_time=datetime.now(timezone.utc),
            total_amount=30.0,
            duration=600,
        )
        await asyncio.sleep(1)

        now = datetime.now(timezone.utc)
        start_time = now.replace(microsecond=0) - timedelta(hours=1)
        end_time = now.replace(microsecond=0) + timedelta(minutes=1)

        intervals = await api.list_pump_intervals(child_uid, start_time, end_time)

        assert isinstance(intervals, list)
        assert len(intervals) >= 1

        for interval in intervals:
            assert isinstance(interval, FirebasePumpIntervalData)
            assert isinstance(interval.start, (int, float))
            assert interval.entryMode in ("leftright", "total")
            assert interval.units in ("ml", "oz")

    async def test_list_activity_intervals(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test fetching activity intervals for a date range."""
        await api.log_activity(
            child_uid,
            mode="storyTime",
            start_time=datetime.now(timezone.utc),
            duration=600,
            notes="calendar activity test",
        )
        await asyncio.sleep(1)

        now = datetime.now(timezone.utc)
        start_time = now.replace(microsecond=0) - timedelta(hours=1)
        end_time = now.replace(microsecond=0) + timedelta(minutes=1)

        intervals = await api.list_activity_intervals(child_uid, start_time, end_time)

        assert isinstance(intervals, list)
        assert len(intervals) >= 1

        for interval in intervals:
            assert isinstance(interval, FirebaseActivityIntervalData)
            assert isinstance(interval.start, (int, float))
            assert interval.mode in (
                "bath",
                "tummyTime",
                "storyTime",
                "screenTime",
                "skinToSkin",
                "outdoorPlay",
                "indoorPlay",
                "brushTeeth",
            )

    async def test_date_range_filtering(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that date range filtering works correctly."""
        # Query for a range far in the past (should return empty or fewer results)
        old_start = datetime.fromtimestamp(0, tz=timezone.utc)
        old_end = datetime.fromtimestamp(1000000, tz=timezone.utc)

        intervals = await api.list_sleep_intervals(child_uid, old_start, old_end)

        # Should return empty list for range in distant past
        assert isinstance(intervals, list)
        assert len(intervals) == 0

    async def test_empty_date_range(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test querying with an empty date range."""
        now = datetime.now(timezone.utc).replace(microsecond=0)

        # Start equals end - empty range
        intervals = await api.list_sleep_intervals(child_uid, now, now)

        assert isinstance(intervals, list)
        # Should return empty since the half-open range is empty when start and end are equal
        assert len(intervals) == 0
