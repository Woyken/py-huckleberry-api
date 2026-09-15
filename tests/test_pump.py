"""Pump feeding tests for Huckleberry API."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from google.cloud import firestore

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import FirebasePumpDocumentData


class TestPumpFeeding:
    """Test pump feeding functionality."""

    async def _get_latest_pump_summary(self, api: HuckleberryAPI, child_uid: str):
        """Read prefs.lastPump directly from the pump root document."""
        db = await api._get_firestore_client()
        pump_doc = await db.collection("pump").document(child_uid).get()
        if not pump_doc.exists:
            return None

        pump_data = pump_doc.to_dict() or {}
        pump_model = FirebasePumpDocumentData.model_validate(pump_data)
        return pump_model.prefs.lastPump if pump_model.prefs else None

    async def _next_start_time(self, api: HuckleberryAPI, child_uid: str) -> datetime:
        """Choose a start time that will become the latest pump entry."""
        minimum_start = time.time()
        latest_pump = await self._get_latest_pump_summary(api, child_uid)
        if latest_pump is not None and latest_pump.start is not None:
            minimum_start = max(minimum_start, float(latest_pump.start) + 60.0)
        return datetime.fromtimestamp(minimum_start, tz=timezone.utc)

    async def _find_recent_pump_interval(
        self,
        api: HuckleberryAPI,
        child_uid: str,
        *,
        created_after: float,
        entry_mode: str,
        units: str,
        left_amount: float | None = None,
        right_amount: float | None = None,
        amount: float | None = None,
    ) -> dict[str, object]:
        """Find the pump interval written by the current test.

        Queries a small set of latest intervals and matches on timestamp and payload
        to avoid cross-test race conditions with other pump writes.
        """
        db = await api._get_firestore_client()
        intervals_ref = db.collection("pump").document(child_uid).collection("intervals")

        for _ in range(10):
            recent_intervals = intervals_ref.order_by("lastUpdated", direction=firestore.Query.DESCENDING).limit(10)
            intervals_list = list(await recent_intervals.get())

            for interval_doc in intervals_list:
                interval_data = interval_doc.to_dict()
                if not interval_data:
                    continue

                start_value = interval_data.get("start")
                if not isinstance(start_value, (int, float)) or float(start_value) < created_after:
                    continue

                if interval_data.get("entryMode") != entry_mode:
                    continue

                if interval_data.get("units") != units:
                    continue

                # Match amounts based on entry mode
                if entry_mode == "leftright":
                    if left_amount is not None and interval_data.get("leftAmount") != left_amount:
                        continue
                    if right_amount is not None and interval_data.get("rightAmount") != right_amount:
                        continue
                elif entry_mode == "total":
                    # For total mode, the amount is stored in leftAmount (no separate 'amount' field)
                    if amount is not None and interval_data.get("leftAmount") != amount / 2.0:
                        continue

                return interval_data

            await asyncio.sleep(0.5)

        # Debug: print last 10 intervals for troubleshooting
        recent_intervals = intervals_ref.order_by("lastUpdated", direction=firestore.Query.DESCENDING).limit(10)
        intervals_list = list(await recent_intervals.get())
        print(f"Recent intervals: {[doc.to_dict() for doc in intervals_list]}")
        raise AssertionError("No matching recent pump interval found")

    async def test_log_pump_total_ml(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging total pump mode with ml units."""
        created_after = time.time()
        await api.log_pump(
            child_uid,
            start_time=await self._next_start_time(api, child_uid),
            total_amount=120,
            units="ml",
            duration=1800.0,
            notes="Morning pumping session",
        )
        await asyncio.sleep(1)

        # Verify the interval was created
        interval = await self._find_recent_pump_interval(
            api,
            child_uid,
            created_after=created_after,
            entry_mode="total",
            units="ml",
            amount=120,
        )
        assert interval is not None
        assert interval["entryMode"] == "total"
        assert interval["leftAmount"] == 60
        assert interval["rightAmount"] == 60
        assert interval["units"] == "ml"
        assert interval["duration"] == 1800.0
        assert interval["notes"] == "Morning pumping session"

        # Verify prefs were updated
        db = await api._get_firestore_client()
        pump_doc = await db.collection("pump").document(child_uid).get()
        data = pump_doc.to_dict()
        assert data is not None
        assert "lastPump" in data.get("prefs", {})
        assert data["prefs"]["lastPump"]["entryMode"] == "total"
        assert data["prefs"]["lastPump"]["leftAmount"] == 60
        assert data["prefs"]["lastPump"]["rightAmount"] == 60

    async def test_log_pump_leftright_ml(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging left/right pump mode with ml units."""
        created_after = time.time()
        await api.log_pump(
            child_uid,
            start_time=await self._next_start_time(api, child_uid),
            left_amount=85.0,
            right_amount=95.0,
            units="ml",
            duration=2100.0,
        )
        await asyncio.sleep(1)

        interval = await self._find_recent_pump_interval(
            api,
            child_uid,
            created_after=created_after,
            entry_mode="leftright",
            units="ml",
            left_amount=85.0,
            right_amount=95.0,
        )
        assert interval is not None
        assert interval["entryMode"] == "leftright"
        assert interval["leftAmount"] == 85.0
        assert interval["rightAmount"] == 95.0

        # Verify prefs were updated
        db = await api._get_firestore_client()
        pump_doc = await db.collection("pump").document(child_uid).get()
        data = pump_doc.to_dict()
        assert data is not None
        assert data["prefs"]["lastPump"]["entryMode"] == "leftright"
        assert data["prefs"]["lastPump"]["leftAmount"] == 85.0
        assert data["prefs"]["lastPump"]["rightAmount"] == 95.0

    async def test_log_pump_ounces(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging pump with ounce units."""
        created_after = time.time()
        await api.log_pump(
            child_uid,
            start_time=await self._next_start_time(api, child_uid),
            total_amount=6.0,
            units="oz",
            duration=1500.0,
        )
        await asyncio.sleep(1)

        interval = await self._find_recent_pump_interval(
            api,
            child_uid,
            created_after=created_after,
            entry_mode="total",
            units="oz",
            amount=6.0,
        )
        assert interval is not None
        assert interval["units"] == "oz"
        assert interval["leftAmount"] == 3.0
        assert interval["rightAmount"] == 3.0

    async def test_log_pump_leftright_ounces(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging left/right pump mode with ounce units."""
        created_after = time.time()
        await api.log_pump(
            child_uid,
            start_time=await self._next_start_time(api, child_uid),
            left_amount=4.5,
            right_amount=4.8,
            units="oz",
        )
        await asyncio.sleep(1)

        interval = await self._find_recent_pump_interval(
            api,
            child_uid,
            created_after=created_after,
            entry_mode="leftright",
            units="oz",
            left_amount=4.5,
            right_amount=4.8,
        )
        assert interval is not None
        assert interval["entryMode"] == "leftright"
        assert interval["leftAmount"] == 4.5
        assert interval["rightAmount"] == 4.8

    async def test_list_pump_intervals(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test listing pump intervals within a date range."""
        from huckleberry_api.firebase_types import FirebasePumpIntervalData

        created_after = time.time()

        # Create some pump intervals
        for i in range(3):
            await api.log_pump(
                child_uid,
                start_time=await self._next_start_time(api, child_uid),
                total_amount=100.0 + i * 20,
                units="ml",
            )
            await asyncio.sleep(0.2)

        # Wait for intervals to be indexed
        await asyncio.sleep(1)

        # Get all intervals, allowing for start times nudged forward to remain newer
        # than an existing latest pump summary in live Firebase.
        end_time = time.time() + 3600.0
        intervals = await api.list_pump_intervals(
            child_uid,
            datetime.fromtimestamp(created_after, tz=timezone.utc),
            datetime.fromtimestamp(end_time, tz=timezone.utc),
        )
        expected_amounts = {50.0, 60.0, 70.0}
        created_intervals = [
            interval
            for interval in intervals
            if float(interval.start) >= created_after
            and interval.entryMode == "total"
            and interval.units == "ml"
            and interval.leftAmount in expected_amounts
            and interval.rightAmount in expected_amounts
        ]
        assert len(created_intervals) >= 3

        # Verify interval data
        assert all(isinstance(interval, FirebasePumpIntervalData) for interval in created_intervals)
        assert all(interval.entryMode == "total" for interval in created_intervals)
        assert all(interval.units == "ml" for interval in created_intervals)

    async def test_log_pump_total_amount(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging a total-mode pump entry."""
        previous_latest = await self._get_latest_pump_summary(api, child_uid)
        minimum_start = time.time()
        if previous_latest is not None and previous_latest.start is not None:
            minimum_start = max(minimum_start, float(previous_latest.start) + 60.0)

        created_after = time.time()
        start_time = datetime.fromtimestamp(minimum_start, tz=timezone.utc)

        await api.log_pump(
            child_uid,
            start_time=start_time,
            duration=900,
            total_amount=40.0,
            units="ml",
            notes="total mode test",
        )
        await asyncio.sleep(2)

        interval_data = await self._find_recent_pump_interval(
            api,
            child_uid,
            created_after=created_after,
            entry_mode="total",
            units="ml",
            amount=40.0,
        )

        assert interval_data["duration"] == 900.0
        assert interval_data["notes"] == "total mode test"
        assert "end_offset" in interval_data
        interval_start = interval_data["start"]
        assert isinstance(interval_start, int | float)
        assert abs(float(interval_start) - start_time.timestamp()) < 2.0

        latest = await self._get_latest_pump_summary(api, child_uid)
        assert latest is not None
        assert latest.duration == 900.0
        assert latest.entryMode == "total"
        assert latest.leftAmount == 20.0
        assert latest.rightAmount == 20.0
        assert latest.units == "ml"

    async def test_log_pump_left_right_requires_both_amounts(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that leftright pump entries require both side amounts."""
        start_time = datetime.now(timezone.utc)

        with pytest.raises(ValueError, match="require both left_amount and right_amount"):
            await api.log_pump(
                child_uid,
                start_time=start_time,
                left_amount=18.5,
                units="oz",
            )

        with pytest.raises(ValueError, match="require both left_amount and right_amount"):
            await api.log_pump(
                child_uid,
                start_time=start_time,
                right_amount=12.0,
                units="oz",
            )

    async def test_log_pump_older_entry_does_not_replace_latest(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that backfilled historical pump entries do not replace prefs.lastPump."""
        previous_latest = await self._get_latest_pump_summary(api, child_uid)
        minimum_recent_start = time.time()
        if previous_latest is not None and previous_latest.start is not None:
            minimum_recent_start = max(minimum_recent_start, float(previous_latest.start) + 60.0)

        recent_start = datetime.fromtimestamp(minimum_recent_start, tz=timezone.utc)
        older_start = recent_start - timedelta(hours=3)

        await api.log_pump(
            child_uid,
            start_time=recent_start,
            total_amount=33.0,
            duration=600,
            units="ml",
        )
        await asyncio.sleep(1)

        latest_after_recent = await self._get_latest_pump_summary(api, child_uid)
        assert latest_after_recent is not None
        assert latest_after_recent.start is not None
        assert abs(float(latest_after_recent.start) - recent_start.timestamp()) < 2.0

        await api.log_pump(
            child_uid,
            start_time=older_start,
            total_amount=11.0,
            duration=300,
            units="ml",
        )
        await asyncio.sleep(1)

        latest_after_older = await self._get_latest_pump_summary(api, child_uid)
        assert latest_after_older is not None
        assert latest_after_older.start is not None
        assert abs(float(latest_after_older.start) - recent_start.timestamp()) < 2.0
        assert latest_after_older.leftAmount == 16.5
        assert latest_after_older.rightAmount == 16.5
        assert latest_after_older.duration == 600.0

    async def test_complete_pump_timer_writes_interval(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test the live start, pause, and complete pumping flow."""
        created_after = time.time()
        db = await api._get_firestore_client()
        pump_ref = db.collection("pump").document(child_uid)
        before = (await pump_ref.get()).to_dict() or {}
        before_prefs = before.get("prefs", {})
        before_timestamp = before_prefs.get("timestamp")
        before_local_timestamp = before_prefs.get("local_timestamp")

        await api.start_pump(child_uid)
        await asyncio.sleep(0.2)
        await api.pause_pump(child_uid)
        await api.complete_pump(
            child_uid,
            total_amount=14.5,
            notes="pump timer integration test",
        )
        await asyncio.sleep(1)

        interval = await self._find_recent_pump_interval(
            api,
            child_uid,
            created_after=created_after,
            entry_mode="total",
            units="ml",
            amount=14.5,
        )
        assert interval["leftAmount"] == 7.25
        assert interval["rightAmount"] == 7.25
        assert interval["notes"] == "pump timer integration test"
        assert isinstance(interval["duration"], int | float)

        pump_doc = await pump_ref.get()
        after = pump_doc.to_dict() or {}
        pump_data = FirebasePumpDocumentData.model_validate(after)
        assert pump_data.timer is not None
        assert pump_data.timer.active is False
        assert pump_data.timer.paused is None
        after_prefs = after.get("prefs", {})
        assert after_prefs.get("timestamp") == before_timestamp
        assert after_prefs.get("local_timestamp") == before_local_timestamp
