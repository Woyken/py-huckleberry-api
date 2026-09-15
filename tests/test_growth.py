"""Growth tracking tests for Huckleberry API."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from google.cloud import firestore

from huckleberry_api import HuckleberryAPI


class TestGrowthTracking:
    """Test growth tracking functionality."""

    async def test_log_growth_metric(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging growth measurement in metric units."""
        await api.log_growth(
            child_uid,
            start_time=datetime.now(timezone.utc).replace(microsecond=0),
            weight=5.2,
            height=52.0,
            head=35.0,
            units="metric",
        )
        await asyncio.sleep(1)

        # Verify it was logged by checking health collection
        db = await api._get_firestore_client()
        health_doc = await db.collection("health").document(child_uid).get()
        data = health_doc.to_dict()
        assert data is not None
        assert "lastGrowthEntry" in data.get("prefs", {})

    async def test_log_growth_imperial(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging supported imperial growth measurements."""
        await api.log_growth(
            child_uid,
            start_time=datetime.now(timezone.utc).replace(microsecond=0),
            weight=11.5,
            head=13.8,
            units="imperial",
        )
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

    async def test_log_growth_with_explicit_start_time(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging growth with an explicit past timestamp."""
        start_time = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=5)

        await api.log_growth(child_uid, start_time=start_time, weight=6.1, units="metric")
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        data_ref = db.collection("health").document(child_uid).collection("data")
        matching = list(await data_ref.where(filter=firestore.FieldFilter("start", "==", start_time.timestamp())).get())

        assert matching
        growth_data = matching[0].to_dict()
        assert growth_data is not None
        assert growth_data["start"] == start_time.timestamp()
        assert growth_data["weight"] == 6.1

    @pytest.mark.integration
    async def test_log_growth_writes_live_history_shape(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Growth history omits fields that belong only to the latest preference."""
        start_time = datetime(2020, 1, 3, 4, 5, 6, tzinfo=timezone.utc)
        db = await api._get_firestore_client()
        health_ref = db.collection("health").document(child_uid)
        health_data = (await health_ref.get()).to_dict() or {}
        latest = health_data.get("prefs", {}).get("lastGrowthEntry")
        if not latest or latest["start"] <= start_time.timestamp():
            pytest.skip("A newer lastGrowthEntry is required for non-destructive integration testing")

        query = health_ref.collection("data").where(filter=firestore.FieldFilter("start", "==", start_time.timestamp()))
        existing_ids = {snapshot.id for snapshot in await query.get()}
        try:
            await api.log_growth(child_uid, start_time=start_time, weight=5.1, units="metric")
            snapshots = await query.get()
            created = [snapshot for snapshot in snapshots if snapshot.id not in existing_ids]
            assert len(created) == 1
            payload = created[0].to_dict()
            assert payload is not None
            assert payload["weight"] == 5.1
            assert payload["weightUnits"] == "kg"
            assert "_id" not in payload
            assert "type" not in payload
            assert "isNight" not in payload
            assert "multientry_key" not in payload
        finally:
            for snapshot in await query.get():
                if snapshot.id not in existing_ids:
                    await snapshot.reference.delete()
