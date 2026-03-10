"""Growth tracking tests for Huckleberry API."""

import asyncio
import random
from datetime import datetime, timezone

from huckleberry_api import HuckleberryAPI


class TestGrowthTracking:
    """Test growth tracking functionality."""

    async def test_log_growth_metric(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging growth measurement in metric units."""
        await api.log_growth(child_uid, weight=5.2, height=52.0, head=35.0, units="metric")
        await asyncio.sleep(1)

        # Verify it was logged by checking health collection
        db = await api._get_firestore_client()
        health_doc = await db.collection("health").document(child_uid).get()
        data = health_doc.to_dict()
        assert data is not None
        assert "lastGrowthEntry" in data.get("prefs", {})

    async def test_log_growth_imperial(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging supported imperial growth measurements."""
        await api.log_growth(child_uid, weight=11.5, head=13.8, units="imperial")
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        health_doc = await db.collection("health").document(child_uid).get()
        data = health_doc.to_dict()
        assert data is not None
        assert "lastGrowthEntry" in data.get("prefs", {})
        last_growth = data["prefs"]["lastGrowthEntry"]
        assert last_growth["weight"] == 11.5
        assert last_growth["weightUnits"] == "lbs.oz"
        assert last_growth["head"] == 13.8
        assert last_growth["headUnits"] == "hin"
        assert "height" not in last_growth
        assert "heightUnits" not in last_growth

    async def test_get_latest_growth(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test retrieving latest growth data."""
        growth_data = await api.get_latest_growth(child_uid)
        if growth_data is None:
            return

        assert growth_data.mode == "growth"
        if growth_data.weightUnits is not None:
            assert growth_data.weightUnits in ("kg", "lbs.oz")
        if growth_data.heightUnits is not None:
            assert growth_data.heightUnits in ("cm", "ft.in")
        if growth_data.headUnits is not None:
            assert growth_data.headUnits in ("hcm", "hin")

    async def test_log_growth_with_timestamp(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that a custom timestamp is stored on the growth entry."""
        past_time = datetime(2024, 2, 14, 8, 0, 0, tzinfo=timezone.utc)
        unique_weight = round(random.uniform(0.1, 30.0), 4)
        await api.log_growth(child_uid, weight=unique_weight, units="metric", timestamp=past_time)
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        health_ref = db.collection("health").document(child_uid)
        data_stream = health_ref.collection("data").where("weight", "==", unique_weight).stream()
        docs = [doc async for doc in data_stream]
        assert len(docs) >= 1, "Expected at least one growth entry with the unique weight"
        assert docs[0].to_dict()["start"] == past_time.timestamp()
