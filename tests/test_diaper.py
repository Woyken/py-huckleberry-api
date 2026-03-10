"""Diaper tracking tests for Huckleberry API."""

import asyncio
import uuid
from datetime import datetime, timezone

from huckleberry_api import HuckleberryAPI


class TestDiaperTracking:
    """Test diaper tracking functionality."""

    async def test_log_diaper_pee(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging pee-only diaper change."""
        await api.log_diaper(child_uid, mode="pee", pee_amount="medium")
        await asyncio.sleep(1)

        # Verify it was logged
        db = await api._get_firestore_client()
        diaper_doc = await db.collection("diaper").document(child_uid).get()
        data = diaper_doc.to_dict()
        assert data is not None
        assert "lastDiaper" in data.get("prefs", {})
        assert data["prefs"]["lastDiaper"]["mode"] == "pee"

    async def test_log_diaper_poo(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging poo-only diaper change."""
        await api.log_diaper(child_uid, mode="poo", poo_amount="big", color="yellow", consistency="solid")
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        diaper_doc = await db.collection("diaper").document(child_uid).get()
        data = diaper_doc.to_dict()
        assert data is not None
        assert data["prefs"]["lastDiaper"]["mode"] == "poo"

    async def test_log_diaper_both(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging both pee and poo."""
        await api.log_diaper(
            child_uid, mode="both", pee_amount="medium", poo_amount="medium", color="green", consistency="runny"
        )
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        diaper_doc = await db.collection("diaper").document(child_uid).get()
        data = diaper_doc.to_dict()
        assert data is not None
        assert data["prefs"]["lastDiaper"]["mode"] == "both"

    async def test_log_diaper_dry(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging dry diaper check."""
        await api.log_diaper(child_uid, mode="dry")
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        diaper_doc = await db.collection("diaper").document(child_uid).get()
        data = diaper_doc.to_dict()
        assert data is not None
        assert data["prefs"]["lastDiaper"]["mode"] == "dry"

    async def test_log_potty(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging a potty event into the shared diaper tracker."""
        await api.log_potty(
            child_uid,
            mode="poo",
            how_it_happened="wentPotty",
            poo_amount="medium",
            color="brown",
            consistency="solid",
            notes="Morning potty success",
        )
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        diaper_ref = db.collection("diaper").document(child_uid)
        diaper_doc = await diaper_ref.get()
        data = diaper_doc.to_dict()
        assert data is not None
        assert data["prefs"]["lastPotty"]["mode"] == "poo"

        intervals = diaper_ref.collection("intervals").order_by("start", direction="DESCENDING").limit(1).stream()
        docs = [doc async for doc in intervals]
        assert docs
        latest = docs[0].to_dict()
        assert latest is not None
        assert latest["isPotty"] is True
        assert latest["howItHappened"] == "wentPotty"

    async def test_log_diaper_with_timestamp(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that a custom timestamp is stored on the interval."""
        past_time = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        unique_note = str(uuid.uuid4())
        await api.log_diaper(child_uid, mode="pee", timestamp=past_time, notes=unique_note)
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        diaper_ref = db.collection("diaper").document(child_uid)
        intervals = diaper_ref.collection("intervals").where("notes", "==", unique_note).stream()
        docs = [doc async for doc in intervals]
        assert len(docs) == 1, "Expected exactly one interval with the unique note"
        assert docs[0].to_dict()["start"] == past_time.timestamp()

    async def test_log_diaper_without_timestamp_uses_current_time(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that omitting timestamp defaults to approximately now."""
        before = datetime.now(timezone.utc).timestamp()
        await api.log_diaper(child_uid, mode="dry")
        after = datetime.now(timezone.utc).timestamp()
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        diaper_ref = db.collection("diaper").document(child_uid)
        intervals = diaper_ref.collection("intervals").order_by("start", direction="DESCENDING").limit(1).stream()
        docs = [doc async for doc in intervals]
        assert docs
        start = docs[0].to_dict()["start"]
        assert before <= start <= after
