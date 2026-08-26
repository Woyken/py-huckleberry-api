"""Medication logging tests for Huckleberry API."""

import asyncio
from datetime import datetime, timedelta, timezone

from google.cloud import firestore

from huckleberry_api import HuckleberryAPI


class TestMedicationLogging:
    """Test medication logging functionality."""

    async def test_log_medication_basic(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging a medication dose with amount and units."""
        await api.log_medication(
            child_uid,
            start_time=datetime.now(timezone.utc).replace(microsecond=0),
            name="Tylenol",
            amount=5.0,
            units="ml",
        )
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        health_doc = await db.collection("health").document(child_uid).get()
        data = health_doc.to_dict()
        assert data is not None
        assert "lastMedication" in data.get("prefs", {})
        last = data["prefs"]["lastMedication"]
        assert last["medication_name"] == "Tylenol"
        assert last["amount"] == 5.0
        assert last["units"] == "ml"
        assert last["mode"] == "medication"

    async def test_log_medication_no_amount(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging a medication dose without amount or units."""
        await api.log_medication(
            child_uid,
            start_time=datetime.now(timezone.utc).replace(microsecond=0),
            name="Vitamin D",
        )
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        health_doc = await db.collection("health").document(child_uid).get()
        data = health_doc.to_dict()
        assert data is not None
        assert "lastMedication" in data.get("prefs", {})
        last = data["prefs"]["lastMedication"]
        assert last["medication_name"] == "Vitamin D"
        assert "amount" not in last
        assert "units" not in last

    async def test_log_medication_with_explicit_start_time(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging a medication with an explicit past timestamp."""
        start_time = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)

        await api.log_medication(
            child_uid,
            start_time=start_time,
            name="Ibuprofen",
            amount=2.5,
            units="ml",
        )
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        data_ref = db.collection("health").document(child_uid).collection("data")
        matching = list(
            await data_ref.where(filter=firestore.FieldFilter("start", "==", start_time.timestamp())).get()
        )

        assert matching
        entry = matching[0].to_dict()
        assert entry is not None
        assert entry["start"] == start_time.timestamp()
        assert entry["medication_name"] == "Ibuprofen"
        assert entry["amount"] == 2.5
        assert entry["mode"] == "medication"

    async def test_log_medication_with_notes(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging a medication with notes."""
        await api.log_medication(
            child_uid,
            start_time=datetime.now(timezone.utc).replace(microsecond=0),
            name="Amoxicillin",
            amount=5.0,
            units="ml",
            notes="Given with food",
        )
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        health_doc = await db.collection("health").document(child_uid).get()
        data = health_doc.to_dict()
        assert data is not None
        last = data.get("prefs", {}).get("lastMedication", {})
        assert last.get("notes") == "Given with food"
