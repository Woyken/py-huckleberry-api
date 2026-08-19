"""Unit tests for revision-safe history record mutations."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import cast

import aiohttp
import pytest
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from huckleberry_api import HuckleberryAPI, HuckleberryRecordConflictError
from huckleberry_api.firebase_types import FirebaseHistoryRecordReference


class FakeSnapshot:
    """Small Firestore snapshot used by history mutation tests."""

    def __init__(self, document_id: str, data: dict[str, object], revision: datetime):
        self.id = document_id
        self._data = deepcopy(data)
        self.update_time = revision
        self.exists = True

    def to_dict(self) -> dict[str, object]:
        return deepcopy(self._data)


class FakeDocumentReference:
    def __init__(self, client: FakeClient, path: tuple[str, ...]):
        self.client = client
        self.path = path

    def collection(self, name: str) -> FakeCollectionReference:
        return FakeCollectionReference(self.client, (*self.path, name))

    async def get(self, transaction=None) -> FakeSnapshot:
        del transaction
        return self.client.documents[self.path]


class FakeQuery:
    def __init__(
        self,
        client: FakeClient,
        path: tuple[str, ...],
        filters: list[FieldFilter] | None = None,
        order_field: str | None = None,
        descending: bool = False,
    ):
        self.client = client
        self.path = path
        self.filters = filters or []
        self.order_field = order_field
        self.descending = descending

    def where(self, *, filter) -> FakeQuery:
        return FakeQuery(
            self.client,
            self.path,
            [*self.filters, filter],
            self.order_field,
            self.descending,
        )

    def order_by(self, field: str, direction=None) -> FakeQuery:
        return FakeQuery(
            self.client,
            self.path,
            self.filters,
            field,
            direction == firestore.Query.DESCENDING,
        )

    async def stream(self, transaction=None):
        del transaction
        snapshots = [snapshot for path, snapshot in self.client.documents.items() if path[:-1] == self.path]
        for field_filter in self.filters:
            filtered: list[FakeSnapshot] = []
            for snapshot in snapshots:
                value = snapshot.to_dict().get(field_filter.field_path)
                if field_filter.op_string == "==":
                    matches = value == field_filter.value
                elif field_filter.op_string == ">=":
                    matches = (
                        isinstance(value, int | float)
                        and isinstance(field_filter.value, int | float)
                        and value >= field_filter.value
                    )
                elif field_filter.op_string == "<":
                    matches = (
                        isinstance(value, int | float)
                        and isinstance(field_filter.value, int | float)
                        and value < field_filter.value
                    )
                else:
                    raise AssertionError(f"Unsupported fake filter: {field_filter.op_string}")
                if matches:
                    filtered.append(snapshot)
            snapshots = filtered
        if self.order_field is not None:
            snapshots = [snapshot for snapshot in snapshots if snapshot.to_dict().get(self.order_field) is not None]
            snapshots.sort(
                key=lambda snapshot: snapshot.to_dict()[cast(str, self.order_field)],
                reverse=self.descending,
            )
        for snapshot in snapshots:
            yield snapshot


class FakeCollectionReference(FakeQuery):
    def document(self, document_id: str) -> FakeDocumentReference:
        return FakeDocumentReference(self.client, (*self.path, document_id))


class FakeTransaction:
    def __init__(self):
        self.operations: list[tuple[str, tuple[str, ...], dict[str, object] | None]] = []

    def set(self, reference: FakeDocumentReference, data: dict[str, object]) -> None:
        self.operations.append(("set", reference.path, deepcopy(data)))

    def update(self, reference: FakeDocumentReference, data: dict[str, object]) -> None:
        self.operations.append(("update", reference.path, deepcopy(data)))

    def delete(self, reference: FakeDocumentReference) -> None:
        self.operations.append(("delete", reference.path, None))


class FakeClient:
    def __init__(self, documents: dict[tuple[str, ...], FakeSnapshot]):
        self.documents = documents
        self.last_transaction: FakeTransaction | None = None

    def collection(self, name: str) -> FakeCollectionReference:
        return FakeCollectionReference(self, (name,))

    def transaction(self) -> FakeTransaction:
        self.last_transaction = FakeTransaction()
        return self.last_transaction


def make_api(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> HuckleberryAPI:
    """Create an API instance backed by the in-memory fake Firestore client."""
    api = HuckleberryAPI(
        email="test@example.com",
        password="not-a-real-password",
        timezone="America/New_York",
        websession=cast(aiohttp.ClientSession, object()),
    )

    async def get_client() -> FakeClient:
        return client

    monkeypatch.setattr(api, "_get_firestore_client", get_client)
    monkeypatch.setattr(firestore, "async_transactional", lambda function: function)
    return api


def revision(value: datetime) -> str:
    return value.isoformat()


async def test_list_sleep_records_include_regular_and_multi_references(monkeypatch: pytest.MonkeyPatch) -> None:
    regular_revision = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    multi_revision = datetime(2026, 8, 18, 12, 1, tzinfo=timezone.utc)
    documents = {
        ("sleep", "child", "intervals", "regular-id"): FakeSnapshot(
            "regular-id",
            {"start": 100.0, "duration": 60.0, "offset": 240.0},
            regular_revision,
        ),
        ("sleep", "child", "intervals", "multi-id"): FakeSnapshot(
            "multi-id",
            {
                "multi": True,
                "data": {"entry-key": {"start": 200.0, "duration": 120.0, "offset": 240.0}},
            },
            multi_revision,
        ),
    }
    api = make_api(monkeypatch, FakeClient(documents))

    records = await api.list_sleep_interval_records(
        "child",
        datetime.fromtimestamp(0, timezone.utc),
        datetime.fromtimestamp(1000, timezone.utc),
    )

    references = {record.reference.document_id: record.reference for record in records}
    assert references["regular-id"].entry_key is None
    assert references["regular-id"].revision == revision(regular_revision)
    assert references["multi-id"].entry_key == "entry-key"
    assert references["multi-id"].revision == revision(multi_revision)


async def test_stale_reference_is_rejected_before_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    current_revision = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    client = FakeClient(
        {
            ("sleep", "child", "intervals", "sleep-id"): FakeSnapshot(
                "sleep-id",
                {"start": 100.0, "duration": 60.0, "offset": 240.0},
                current_revision,
            )
        }
    )
    api = make_api(monkeypatch, client)
    stale_reference = FirebaseHistoryRecordReference(
        document_id="sleep-id",
        revision=revision(datetime(2026, 8, 18, 11, 59, tzinfo=timezone.utc)),
    )

    with pytest.raises(HuckleberryRecordConflictError):
        await api.delete_sleep_interval("child", stale_reference)

    assert client.last_transaction is not None
    assert client.last_transaction.operations == []


async def test_delete_repairs_last_sleep_in_same_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    target_revision = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    documents = {
        ("sleep", "child", "intervals", "latest-id"): FakeSnapshot(
            "latest-id",
            {"start": 200.0, "duration": 60.0, "offset": 240.0},
            target_revision,
        ),
        ("sleep", "child", "intervals", "previous-id"): FakeSnapshot(
            "previous-id",
            {"start": 100.0, "duration": 30.0, "offset": 240.0},
            datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc),
        ),
    }
    client = FakeClient(documents)
    api = make_api(monkeypatch, client)
    reference = FirebaseHistoryRecordReference(
        document_id="latest-id",
        revision=revision(target_revision),
    )

    await api.delete_sleep_interval("child", reference)

    assert client.last_transaction is not None
    operations = client.last_transaction.operations
    assert ("delete", ("sleep", "child", "intervals", "latest-id"), None) in operations
    root_update = next(
        data for operation, path, data in operations if operation == "update" and path == ("sleep", "child")
    )
    assert root_update is not None
    assert root_update["prefs.lastSleep"] == {"start": 100.0, "duration": 30.0, "offset": 240.0}


async def test_update_multi_bottle_preserves_container_and_repairs_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    target_revision = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    documents = {
        ("feed", "child", "intervals", "multi-id"): FakeSnapshot(
            "multi-id",
            {
                "multi": True,
                "hasMoreRoom": True,
                "data": {
                    "target": {
                        "mode": "bottle",
                        "start": 200.0,
                        "lastUpdated": 200.0,
                        "bottleType": "Formula",
                        "amount": 90.0,
                        "units": "ml",
                        "offset": 240.0,
                    },
                    "other": {
                        "mode": "bottle",
                        "start": 100.0,
                        "lastUpdated": 100.0,
                        "bottleType": "Breast Milk",
                        "amount": 60.0,
                        "units": "ml",
                        "offset": 240.0,
                    },
                },
            },
            target_revision,
        )
    }
    client = FakeClient(documents)
    api = make_api(monkeypatch, client)
    reference = FirebaseHistoryRecordReference(
        document_id="multi-id",
        entry_key="target",
        revision=revision(target_revision),
    )

    await api.update_bottle_interval(
        "child",
        reference,
        start_time=datetime(2026, 8, 18, 8, 30, tzinfo=timezone.utc),
        amount=120.0,
        bottle_type="Breast Milk",
        units="ml",
    )

    assert client.last_transaction is not None
    operations = client.last_transaction.operations
    container_write = next(
        data for operation, path, data in operations if operation == "set" and path[-1] == "multi-id"
    )
    assert container_write is not None
    container_entries = cast(dict[str, dict[str, object]], container_write["data"])
    assert container_entries["target"]["amount"] == 120.0
    assert container_entries["other"]["amount"] == 60.0

    root_update = next(
        data for operation, path, data in operations if operation == "update" and path == ("feed", "child")
    )
    assert root_update is not None
    assert cast(dict[str, object], root_update["prefs.lastBottle"])["bottleAmount"] == 120.0


async def test_delete_only_bottle_clears_last_bottle_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    target_revision = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    client = FakeClient(
        {
            ("feed", "child", "intervals", "bottle-id"): FakeSnapshot(
                "bottle-id",
                {
                    "mode": "bottle",
                    "start": 200.0,
                    "lastUpdated": 200.0,
                    "bottleType": "Formula",
                    "amount": 90.0,
                    "units": "ml",
                    "offset": 240.0,
                },
                target_revision,
            )
        }
    )
    api = make_api(monkeypatch, client)
    reference = FirebaseHistoryRecordReference(
        document_id="bottle-id",
        revision=revision(target_revision),
    )

    await api.delete_feed_interval("child", reference)

    assert client.last_transaction is not None
    root_update = next(
        data
        for operation, path, data in client.last_transaction.operations
        if operation == "update" and path == ("feed", "child")
    )
    assert root_update is not None
    assert root_update["prefs.lastBottle"] is firestore.DELETE_FIELD


async def test_update_diaper_replaces_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    target_revision = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    client = FakeClient(
        {
            ("diaper", "child", "intervals", "diaper-id"): FakeSnapshot(
                "diaper-id",
                {
                    "mode": "both",
                    "start": 200.0,
                    "lastUpdated": 200.0,
                    "offset": 240.0,
                    "color": "yellow",
                    "consistency": "solid",
                    "diaperRash": True,
                    "notes": "old note",
                },
                target_revision,
            )
        }
    )
    api = make_api(monkeypatch, client)
    reference = FirebaseHistoryRecordReference(
        document_id="diaper-id",
        revision=revision(target_revision),
    )

    await api.update_diaper_interval(
        "child",
        reference,
        start_time=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
        mode="pee",
        pee_amount="big",
    )

    assert client.last_transaction is not None
    interval_write = next(
        data
        for operation, path, data in client.last_transaction.operations
        if operation == "set" and path[-1] == "diaper-id"
    )
    assert interval_write is not None
    assert interval_write["mode"] == "pee"
    assert interval_write["quantity"] == {"pee": 100.0}
    assert "color" not in interval_write
    assert "consistency" not in interval_write
    assert "diaperRash" not in interval_write
    assert "notes" not in interval_write


async def test_update_growth_replaces_measurements_and_units(monkeypatch: pytest.MonkeyPatch) -> None:
    target_revision = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    client = FakeClient(
        {
            ("health", "child", "data", "growth-id"): FakeSnapshot(
                "growth-id",
                {
                    "_id": "growth-id",
                    "type": "health",
                    "mode": "growth",
                    "start": 200.0,
                    "lastUpdated": 200.0,
                    "offset": 240.0,
                    "weight": 5.0,
                    "weightUnits": "kg",
                    "height": 50.0,
                    "heightUnits": "cm",
                },
                target_revision,
            )
        }
    )
    api = make_api(monkeypatch, client)
    reference = FirebaseHistoryRecordReference(
        document_id="growth-id",
        revision=revision(target_revision),
    )

    await api.update_growth_entry(
        "child",
        reference,
        start_time=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc),
        weight=12.0,
        head=15.0,
        units="imperial",
    )

    assert client.last_transaction is not None
    interval_write = next(
        data
        for operation, path, data in client.last_transaction.operations
        if operation == "set" and path[-1] == "growth-id"
    )
    assert interval_write is not None
    assert interval_write["weight"] == 12.0
    assert interval_write["weightUnits"] == "lbs.oz"
    assert interval_write["head"] == 15.0
    assert interval_write["headUnits"] == "hin"
    assert "height" not in interval_write
    assert "heightUnits" not in interval_write


async def test_edit_validation_happens_before_firestore(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient({})
    api = make_api(monkeypatch, client)
    reference = FirebaseHistoryRecordReference(document_id="sleep-id", revision="revision")

    with pytest.raises(ValueError, match="timezone"):
        await api.update_sleep_interval(
            "child",
            reference,
            start_time=datetime(2026, 8, 18, 8, 0),
            end_time=datetime(2026, 8, 18, 9, 0),
        )

    assert client.last_transaction is None
