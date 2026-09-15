"""Medicine type and dose logging tests."""

import time
from datetime import datetime, timezone
from typing import Literal, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.cloud import firestore

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import (
    FirebaseMedicationData,
    FirebaseMedicationTypeDocument,
)


def _medicine_api(websession, monkeypatch, health_payload: dict[str, object] | None = None):
    snapshot = MagicMock()
    snapshot.to_dict.return_value = health_payload or {}

    data_document = MagicMock()
    data_document.set = AsyncMock()
    data_collection = MagicMock()
    data_collection.document.return_value = data_document

    type_document = MagicMock()
    type_document.set = AsyncMock()
    types_collection = MagicMock()
    types_collection.document.return_value = type_document

    health_document = MagicMock()
    health_document.get = AsyncMock(return_value=snapshot)
    health_document.update = AsyncMock()
    health_document.collection.side_effect = lambda name: {
        "data": data_collection,
        "types": types_collection,
    }[name]

    client = MagicMock()
    client.collection.return_value.document.return_value = health_document

    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=client))
    monkeypatch.setattr(api, "_get_timezone_offset_minutes", AsyncMock(return_value=-180))
    return api, health_document, data_document, type_document


async def test_create_medicine_type_writes_live_shape(websession, monkeypatch) -> None:
    api, _, _, type_document = _medicine_api(websession, monkeypatch)

    created = await api.create_medicine_type("child", "  Vitamin D  ")

    assert created.name == "Vitamin D"
    type_document.set.assert_awaited_once_with(
        {
            "_id": created.id_,
            "active": True,
            "mode": "medication",
            "name": "Vitamin D",
        }
    )


def test_medicine_models_distinguish_live_storage_contexts() -> None:
    batched = FirebaseMedicationData.model_validate(
        {
            "mode": "medication",
            "start": 1773640771.81,
            "offset": -120,
            "medication_id": "medicine-id",
            "medication_name": "Vitamin D",
            "amount": 0.08,
            "units": "oz",
            "notes": "",
        }
    )
    pristine_type = FirebaseMedicationTypeDocument.model_validate(
        {
            "_id": "medicine-id",
            "active": True,
            "mode": "medication",
            "name": "Vitamin D",
        }
    )
    blank_last_take = FirebaseMedicationTypeDocument.model_validate(
        {
            "_id": "medicine-id",
            "active": True,
            "mode": "medication",
            "name": "Vitamin D",
            "lastTake": {"reminderType": "at"},
        }
    )

    assert batched.lastUpdated is None
    assert pristine_type.lastTake is None
    assert blank_last_take.lastTake is not None
    assert blank_last_take.lastTake.amount is None
    assert blank_last_take.lastTake.units is None


async def test_log_medicine_writes_history_type_and_latest(websession, monkeypatch) -> None:
    api, health_document, data_document, type_document = _medicine_api(websession, monkeypatch)
    taken_at = datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc)

    await api.log_medicine(
        "child",
        start_time=taken_at,
        medicine_type=FirebaseMedicationTypeDocument(
            _id="medicine-id",
            active=True,
            mode="medication",
            name="Vitamin D",
        ),
        amount=1.2,
        units="tsp",
        notes="After dinner",
    )

    history = data_document.set.await_args.args[0]
    assert history == {
        "mode": "medication",
        "start": taken_at.timestamp(),
        "lastUpdated": history["lastUpdated"],
        "offset": -180,
        "medication_id": "medicine-id",
        "medication_name": "Vitamin D",
        "amount": 1.2,
        "units": "tsp",
        "notes": "After dinner",
    }
    type_document.set.assert_awaited_once_with(
        {
            "lastTake": {
                "amount": 1.2,
                "reminderType": "at",
                "units": "tsp",
            }
        },
        merge=True,
    )
    latest = health_document.update.await_args.args[0]["prefs.lastMedication"]
    assert latest == {
        **history,
        "_id": latest["_id"],
        "type": "health",
        "isNight": False,
        "multientry_key": None,
    }


async def test_log_medicine_does_not_replace_newer_latest(websession, monkeypatch) -> None:
    api, health_document, _, type_document = _medicine_api(
        websession,
        monkeypatch,
        {
            "prefs": {
                "lastMedication": {
                    "_id": "newer",
                    "type": "health",
                    "mode": "medication",
                    "start": datetime(2026, 9, 16, tzinfo=timezone.utc).timestamp(),
                    "lastUpdated": datetime(2026, 9, 16, tzinfo=timezone.utc).timestamp(),
                    "offset": 0,
                    "medication_id": "existing-id",
                    "medication_name": "Existing",
                    "amount": 1.0,
                    "units": "ml",
                    "notes": "",
                    "isNight": False,
                    "multientry_key": None,
                }
            }
        },
    )

    await api.log_medicine(
        "child",
        start_time=datetime(2026, 9, 15, tzinfo=timezone.utc),
        medicine_type=FirebaseMedicationTypeDocument(
            _id="medicine-id",
            active=True,
            mode="medication",
            name="Vitamin D",
        ),
        amount=2,
        units="drops",
    )

    health_document.update.assert_not_awaited()
    type_document.set.assert_awaited_once()


async def test_log_medicine_without_amount_matches_live_omitted_fields(websession, monkeypatch) -> None:
    api, health_document, data_document, type_document = _medicine_api(
        websession,
        monkeypatch,
        {
            "prefs": {
                "lastMedication": {
                    "_id": "existing",
                    "type": "health",
                    "mode": "medication",
                    "start": datetime(2026, 9, 14, tzinfo=timezone.utc).timestamp(),
                    "lastUpdated": datetime(2026, 9, 14, tzinfo=timezone.utc).timestamp(),
                    "offset": 0,
                    "medication_id": "existing-id",
                    "medication_name": "Existing",
                    "amount": 1.2,
                    "units": "tsp",
                    "notes": "",
                    "isNight": False,
                    "multientry_key": None,
                }
            }
        },
    )

    await api.log_medicine(
        "child",
        start_time=datetime(2026, 9, 15, tzinfo=timezone.utc),
        medicine_type=FirebaseMedicationTypeDocument(
            _id="new-id",
            active=True,
            mode="medication",
            name="New",
        ),
    )

    history = data_document.set.await_args.args[0]
    assert history["amount"] == 0.0
    assert history["notes"] == ""
    assert "units" not in history
    type_document.set.assert_awaited_once_with(
        {"lastTake": {"reminderType": "at"}},
        merge=True,
    )
    latest = health_document.update.await_args.args[0]["prefs.lastMedication"]
    assert latest["amount"] == 0.0
    assert latest["units"] == "tsp"


async def test_log_medicine_requires_units_with_amount(websession, monkeypatch) -> None:
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    get_client = AsyncMock()
    monkeypatch.setattr(api, "_get_firestore_client", get_client)

    with pytest.raises(ValueError, match="units are required"):
        await api.log_medicine(
            "child",
            start_time=datetime(2026, 9, 15, tzinfo=timezone.utc),
            medicine_type=FirebaseMedicationTypeDocument(
                _id="medicine-id",
                active=True,
                mode="medication",
                name="Vitamin D",
            ),
            amount=1,
        )

    get_client.assert_not_awaited()


async def test_log_medicine_rejects_unknown_units_before_firebase(websession, monkeypatch) -> None:
    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    get_client = AsyncMock()
    monkeypatch.setattr(api, "_get_firestore_client", get_client)
    invalid_units = cast(Literal["ml", "oz", "tsp", "drops"], "tbsp")

    with pytest.raises(ValueError, match="literal_error"):
        await api.log_medicine(
            "child",
            start_time=datetime(2026, 9, 15, tzinfo=timezone.utc),
            medicine_type=FirebaseMedicationTypeDocument(
                _id="medicine-id",
                active=True,
                mode="medication",
                name="Vitamin D",
            ),
            amount=1,
            units=invalid_units,
        )

    get_client.assert_not_awaited()


@pytest.mark.integration
async def test_medicine_type_and_dose_live_round_trip(api: HuckleberryAPI, child_uid: str) -> None:
    health_ref = (await api._get_firestore_client()).collection("health").document(child_uid)
    health_data = (await health_ref.get()).to_dict() or {}
    latest = (health_data.get("prefs") or {}).get("lastMedication")
    start_time = datetime(2020, 1, 4, 5, 6, 7, tzinfo=timezone.utc)
    if not isinstance(latest, dict) or latest.get("start", 0) <= start_time.timestamp() + 10:
        pytest.skip("A newer lastMedication is required for non-destructive integration testing")

    medicine_type = await api.create_medicine_type(child_uid, f"api-med-{int(time.time())}")
    event_times: list[datetime] = []
    try:
        listed_types = await api.list_medicine_types(child_uid)
        assert any(item.id_ == medicine_type.id_ for item in listed_types)

        cases: list[tuple[float | None, Literal["ml", "oz", "tsp", "drops"] | None]] = [
            (1.25, "ml"),
            (2.5, "oz"),
            (3.75, "tsp"),
            (4.0, "drops"),
            (None, None),
        ]
        for index, (amount, units) in enumerate(cases):
            event_time = start_time.replace(second=start_time.second + index)
            event_times.append(event_time)
            await api.log_medicine(
                child_uid,
                start_time=event_time,
                medicine_type=medicine_type,
                amount=amount,
                units=units,
                notes=f"medicine integration {index}",
            )
            query = health_ref.collection("data").where(
                filter=firestore.FieldFilter("start", "==", event_time.timestamp())
            )
            snapshots = await query.get()
            assert len(snapshots) == 1
            payload = snapshots[0].to_dict()
            assert payload is not None
            assert payload["amount"] == (0.0 if amount is None else amount)
            assert payload["notes"] == f"medicine integration {index}"
            if units is None:
                assert "units" not in payload
            else:
                assert payload["units"] == units

        type_snapshot = await health_ref.collection("types").document(medicine_type.id_).get()
        stored_type = FirebaseMedicationTypeDocument.model_validate(type_snapshot.to_dict())
        assert stored_type.lastTake is not None
        assert stored_type.lastTake.amount == 4.0
        assert stored_type.lastTake.units == "drops"
    finally:
        for event_time in event_times:
            query = health_ref.collection("data").where(
                filter=firestore.FieldFilter("start", "==", event_time.timestamp())
            )
            for snapshot in await query.get():
                await snapshot.reference.delete()
        await health_ref.collection("types").document(medicine_type.id_).delete()
