"""Bottle feeding tests for Huckleberry API."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

from google.cloud import firestore

from huckleberry_api import HuckleberryAPI


class TestBottleFeeding:
    """Test bottle feeding functionality."""

    async def _next_bottle_start_time(self, api: HuckleberryAPI, child_uid: str) -> datetime:
        """Return a bottle timestamp newer than the current latest summary."""
        db = await api._get_firestore_client()
        feed_doc = await db.collection("feed").document(child_uid).get()
        data = feed_doc.to_dict() or {}
        last_bottle = ((data.get("prefs") or {}).get("lastBottle") or {}).get("start")
        minimum_start = time.time()
        if isinstance(last_bottle, int | float):
            minimum_start = max(minimum_start, float(last_bottle) + 60.0)
        return datetime.fromtimestamp(minimum_start, tz=timezone.utc).replace(microsecond=0)

    async def _find_recent_bottle_interval(
        self,
        api: HuckleberryAPI,
        child_uid: str,
        *,
        created_after: float,
        bottle_type: str,
        amount: float,
        units: str,
    ) -> dict[str, object]:
        """Find the bottle interval written by the current test.

        Queries a small set of latest intervals and matches on timestamp and payload
        to avoid cross-test race conditions with other feed writes.
        """
        db = await api._get_firestore_client()
        intervals_ref = db.collection("feed").document(child_uid).collection("intervals")

        for _ in range(10):
            recent_intervals = intervals_ref.order_by("start", direction=firestore.Query.DESCENDING).limit(10)
            intervals_list = list(await recent_intervals.get())

            for interval_doc in intervals_list:
                interval_data = interval_doc.to_dict()
                if not interval_data:
                    continue

                if interval_data.get("mode") != "bottle":
                    continue

                start_value = interval_data.get("start")
                if not isinstance(start_value, (int, float)) or float(start_value) < created_after:
                    continue

                if (
                    interval_data.get("bottleType") == bottle_type
                    and interval_data.get("amount") == amount
                    and interval_data.get("units") == units
                ):
                    return interval_data

            await asyncio.sleep(0.5)

        raise AssertionError("No matching recent bottle interval found")

    async def test_log_bottle_formula(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging formula bottle feeding."""
        # Log formula bottle
        start_time = await self._next_bottle_start_time(api, child_uid)
        created_after = start_time.timestamp()
        await api.log_bottle(child_uid, start_time=start_time, amount=120.0, bottle_type="Formula", units="ml")
        await asyncio.sleep(2)

        interval_data = await self._find_recent_bottle_interval(
            api,
            child_uid,
            created_after=created_after,
            bottle_type="Formula",
            amount=120.0,
            units="ml",
        )
        assert interval_data["mode"] == "bottle"
        assert interval_data["bottleType"] == "Formula"
        assert interval_data["amount"] == 120.0
        assert interval_data["units"] == "ml"
        assert "start" in interval_data
        assert "lastUpdated" in interval_data
        assert "offset" in interval_data

        # Check prefs.lastBottle updated
        db = await api._get_firestore_client()
        feed_doc = await db.collection("feed").document(child_uid).get()
        data = feed_doc.to_dict()
        assert data is not None
        prefs = data.get("prefs", {})
        assert "lastBottle" in prefs
        assert prefs["lastBottle"]["start"] == start_time.timestamp()
        assert prefs["lastBottle"]["mode"] == "bottle"
        assert prefs["lastBottle"]["bottleType"] == "Formula"
        assert prefs["lastBottle"]["bottleAmount"] == 120.0
        assert prefs["lastBottle"]["bottleUnits"] == "ml"

    async def test_log_bottle_breast_milk(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging breast milk bottle feeding."""
        # Log breast milk bottle
        start_time = await self._next_bottle_start_time(api, child_uid)
        created_after = start_time.timestamp()
        await api.log_bottle(child_uid, start_time=start_time, amount=90.0, bottle_type="Breast Milk", units="ml")
        await asyncio.sleep(2)

        interval_data = await self._find_recent_bottle_interval(
            api,
            child_uid,
            created_after=created_after,
            bottle_type="Breast Milk",
            amount=90.0,
            units="ml",
        )
        assert interval_data["mode"] == "bottle"
        assert interval_data["bottleType"] == "Breast Milk"
        assert interval_data["amount"] == 90.0
        assert interval_data["units"] == "ml"

    async def test_log_bottle_ounces(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging bottle feeding with ounces."""
        # Log with oz units
        start_time = await self._next_bottle_start_time(api, child_uid)
        created_after = time.time()
        await api.log_bottle(child_uid, start_time=start_time, amount=4.0, bottle_type="Formula", units="oz")
        await asyncio.sleep(2)

        interval_data = await self._find_recent_bottle_interval(
            api,
            child_uid,
            created_after=created_after,
            bottle_type="Formula",
            amount=4.0,
            units="oz",
        )
        assert interval_data["units"] == "oz"
        assert interval_data["amount"] == 4.0

    async def test_log_bottle_cow_milk(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging cow milk bottle feeding."""
        # Log cow milk bottle
        start_time = await self._next_bottle_start_time(api, child_uid)
        created_after = time.time()
        await api.log_bottle(child_uid, start_time=start_time, amount=100.0, bottle_type="Cow Milk", units="ml")
        await asyncio.sleep(2)

        interval_data = await self._find_recent_bottle_interval(
            api,
            child_uid,
            created_after=created_after,
            bottle_type="Cow Milk",
            amount=100.0,
            units="ml",
        )
        assert interval_data["mode"] == "bottle"
        assert interval_data["bottleType"] == "Cow Milk"
        assert interval_data["amount"] == 100.0

    async def test_log_bottle_default_params(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging bottle feeding with default parameters."""
        # Log with defaults (Formula, ml)
        start_time = await self._next_bottle_start_time(api, child_uid)
        created_after = time.time()
        await api.log_bottle(child_uid, start_time=start_time, amount=150.0)
        await asyncio.sleep(2)

        interval_data = await self._find_recent_bottle_interval(
            api,
            child_uid,
            created_after=created_after,
            bottle_type="Formula",
            amount=150.0,
            units="ml",
        )
        assert interval_data["mode"] == "bottle"
        assert interval_data["bottleType"] == "Formula"  # Default
        assert interval_data["units"] == "ml"  # Default
        assert interval_data["amount"] == 150.0

    async def test_bottle_feeding_updates_prefs(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that bottle feeding updates document-level preferences."""
        # Log bottle feeding
        start_time = await self._next_bottle_start_time(api, child_uid)
        await api.log_bottle(
            child_uid,
            start_time=start_time,
            amount=110.0,
            bottle_type="Breast Milk",
            units="oz",
        )
        await asyncio.sleep(2)

        # Check document-level prefs updated
        db = await api._get_firestore_client()
        feed_doc = await db.collection("feed").document(child_uid).get()
        data = feed_doc.to_dict()
        assert data is not None
        prefs = data.get("prefs", {})

        # Check document-level defaults
        assert prefs.get("lastBottle", {}).get("start") == start_time.timestamp()
        assert prefs.get("bottleType") == "Breast Milk"
        assert prefs.get("bottleAmount") == 110.0
        assert prefs.get("bottleUnits") == "oz"

        # Check lastBottle
        assert "lastBottle" in prefs
        assert prefs["lastBottle"]["bottleType"] == "Breast Milk"
        assert prefs["lastBottle"]["bottleAmount"] == 110.0
        assert prefs["lastBottle"]["bottleUnits"] == "oz"

    async def test_log_bottle_with_explicit_start_time(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging bottle feeding at an explicit past timestamp."""
        start_time = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)

        await api.log_bottle(
            child_uid,
            start_time=start_time,
            amount=75.0,
            bottle_type="Formula",
            units="ml",
            notes="Left 1.5 oz",
        )
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        intervals_ref = db.collection("feed").document(child_uid).collection("intervals")
        matching = list(
            await intervals_ref.where(filter=firestore.FieldFilter("start", "==", start_time.timestamp())).get()
        )
        bottle_entries = [doc.to_dict() for doc in matching if (doc.to_dict() or {}).get("mode") == "bottle"]

        assert bottle_entries
        interval_data = bottle_entries[0]
        assert interval_data is not None
        assert interval_data["amount"] == 75.0
        assert interval_data["bottleType"] == "Formula"
        assert interval_data["notes"] == "Left 1.5 oz"

    async def test_older_bottle_entry_does_not_replace_latest_summary(
        self, api: HuckleberryAPI, child_uid: str
    ) -> None:
        """Test that backfilled bottle entries do not overwrite the latest bottle summary."""
        newer_start = await self._next_bottle_start_time(api, child_uid)
        older_start = newer_start - timedelta(hours=4)

        await api.log_bottle(
            child_uid,
            start_time=newer_start,
            amount=120.0,
            bottle_type="Breast Milk",
            units="ml",
        )
        await asyncio.sleep(1)
        await api.log_bottle(
            child_uid,
            start_time=older_start,
            amount=60.0,
            bottle_type="Formula",
            units="ml",
        )
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        feed_doc = await db.collection("feed").document(child_uid).get()
        data = feed_doc.to_dict() or {}
        last_bottle = (data.get("prefs") or {}).get("lastBottle") or {}

        assert last_bottle.get("start") == newer_start.timestamp()
        assert last_bottle.get("bottleType") == "Breast Milk"
        assert last_bottle.get("bottleAmount") == 120.0
