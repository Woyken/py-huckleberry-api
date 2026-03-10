"""Solids feeding tests for Huckleberry API."""

import asyncio
import time
import uuid
from datetime import datetime, timezone

from google.cloud import firestore

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import FirebaseSolidsFeedIntervalData
from huckleberry_api.models import SolidsFoodReference


class TestSolidsFeeding:
    """Test solid food feeding functionality."""

    async def test_get_curated_foods(self, api: HuckleberryAPI) -> None:
        """Test fetching curated solids catalog."""
        curated = await api.list_solids_curated_foods()

        assert len(curated) > 0
        first = curated[0]
        assert first.id
        assert first.name
        assert first.source == "curated"

    async def test_create_and_get_custom_foods(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test creating custom food and reading list from Firestore."""
        unique_name = f"api-test-{int(time.time())}"
        created = await api.create_solids_custom_food(child_uid, unique_name)

        assert created.id
        assert created.name == unique_name
        assert created.source == "custom"

        custom_foods = await api.list_solids_custom_foods(child_uid)
        assert any(food.id == created.id for food in custom_foods)

    async def test_log_solids_single_curated_food(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging solids with an existing curated food ID."""
        curated = await api.list_solids_curated_foods()
        assert curated

        await api.log_solids(
            child_uid,
            foods=[SolidsFoodReference(id=curated[0].id, source="curated", name=curated[0].name, amount="small")],
        )
        await asyncio.sleep(2)

        db = await api._get_firestore_client()

        intervals_ref = db.collection("feed").document(child_uid).collection("intervals")

        recent_intervals = intervals_ref.order_by("start", direction=firestore.Query.DESCENDING).limit(1)

        intervals_list = list(await recent_intervals.get())
        assert len(intervals_list) > 0

        data = intervals_list[0].to_dict()
        assert data is not None
        assert data["mode"] == "solids"
        assert "start" in data
        assert "lastUpdated" in data
        assert "offset" in data
        assert "foods" in data

        feed_doc = (await db.collection("feed").document(child_uid).get()).to_dict() or {}
        last_solid = (feed_doc.get("prefs") or {}).get("lastSolid") or {}
        assert last_solid.get("mode") == "solids"
        assert isinstance(last_solid.get("start"), (int, float))
        assert isinstance(last_solid.get("foods"), dict)

        # Check foods structure
        foods = data["foods"]
        assert len(foods) == 1
        food_entry = next(iter(foods.values()))
        assert food_entry["source"] == "curated"
        assert isinstance(food_entry["created_name"], str)
        assert food_entry["created_name"]

    async def test_log_solids_multiple_foods_with_custom_and_curated(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging solids with mixed curated/custom foods, notes, and reaction."""
        custom_food = await api.create_solids_custom_food(child_uid, f"api-custom-{int(time.time())}")
        curated = await api.list_solids_curated_foods()
        assert len(curated) >= 2

        await api.log_solids(
            child_uid,
            foods=[
                SolidsFoodReference(id=curated[0].id, source="curated", name=curated[0].name, amount="small"),
                SolidsFoodReference(id=curated[1].id, source="curated", name=curated[1].name, amount="medium"),
                SolidsFoodReference(id=custom_food.id, source="custom", name=custom_food.name, amount="small"),
            ],
            notes="First time trying broccoli",
            reaction="LOVED",
        )
        await asyncio.sleep(2)

        db = await api._get_firestore_client()

        intervals_ref = db.collection("feed").document(child_uid).collection("intervals")

        recent_intervals = intervals_ref.order_by("start", direction=firestore.Query.DESCENDING).limit(1)

        intervals_list = list(await recent_intervals.get())
        assert len(intervals_list) > 0

        data = intervals_list[0].to_dict()
        assert data is not None
        assert data["mode"] == "solids"
        assert len(data["foods"]) == 3
        assert data.get("notes") == "First time trying broccoli"
        assert data.get("reactions") == {"LOVED": True}

        feed_doc = (await db.collection("feed").document(child_uid).get()).to_dict() or {}
        last_solid = (feed_doc.get("prefs") or {}).get("lastSolid") or {}
        assert last_solid.get("mode") == "solids"
        assert last_solid.get("notes") == "First time trying broccoli"
        assert last_solid.get("reactions") == {"LOVED": True}
        assert isinstance(last_solid.get("foods"), dict)

        sources = {v["source"] for v in data["foods"].values()}
        assert "curated" in sources
        assert "custom" in sources

    async def test_solids_entries_are_in_feed_intervals(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test retrieving solids entries via feed intervals."""
        curated = await api.list_solids_curated_foods()
        await api.log_solids(
            child_uid,
            foods=[SolidsFoodReference(id=curated[0].id, source="curated", name=curated[0].name, amount="small")],
        )
        await asyncio.sleep(2)

        end_ts = int(time.time()) + 60
        start_ts = end_ts - 300

        entries = await api.list_feed_intervals(child_uid, start_ts, end_ts)
        solids_entries = [entry for entry in entries if isinstance(entry, FirebaseSolidsFeedIntervalData)]
        assert len(solids_entries) > 0
        assert solids_entries[-1].foods is not None

    async def test_solids_in_feed_interval_events(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that solids entries appear in feed interval queries."""
        curated = await api.list_solids_curated_foods()
        await api.log_solids(
            child_uid,
            foods=[SolidsFoodReference(id=curated[0].id, source="curated", name=curated[0].name, amount="small")],
        )
        await asyncio.sleep(2)

        end_ts = int(time.time()) + 60
        start_ts = end_ts - 300

        feed_entries = await api.list_feed_intervals(child_uid, start_ts, end_ts)
        assert any(entry.mode == "solids" for entry in feed_entries)

    async def test_log_solids_with_start_time(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that a custom start_time is stored on the solids interval."""
        curated = await api.list_solids_curated_foods()
        assert curated

        past_time = datetime(2024, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
        unique_note = str(uuid.uuid4())
        await api.log_solids(
            child_uid,
            foods=[SolidsFoodReference(id=curated[0].id, source="curated", name=curated[0].name, amount="small")],
            notes=unique_note,
            start_time=past_time,
        )
        await asyncio.sleep(2)

        db = await api._get_firestore_client()
        intervals_ref = db.collection("feed").document(child_uid).collection("intervals")
        intervals = intervals_ref.where("notes", "==", unique_note).stream()
        docs = [doc async for doc in intervals]
        assert len(docs) == 1, "Expected exactly one interval with the unique note"
        assert docs[0].to_dict()["start"] == past_time.timestamp()
