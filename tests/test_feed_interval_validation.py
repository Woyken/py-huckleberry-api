"""Unit tests for `list_feed_intervals` validation of app-written feed rows."""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import FirebaseBottleFeedIntervalData


class _FakeDocument:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return self._payload


class _FakeQuery:
    def __init__(self, documents: dict[str, list[_FakeDocument]]) -> None:
        self._documents = documents
        self._ordered = False

    def where(self, filter: object = None) -> "_FakeQuery":
        return self

    def order_by(self, field_name: str) -> "_FakeQuery":
        self._ordered = True
        return self

    def stream(self) -> Any:
        # The regular-document query is the only one that sorts by `start`.
        docs = self._documents["regular"] if self._ordered else self._documents["multi"]

        async def _iterate() -> Any:
            for doc in docs:
                yield doc

        return _iterate()


class _FakeIntervalsCollection:
    def __init__(self, documents: dict[str, list[_FakeDocument]]) -> None:
        self._documents = documents

    def where(self, filter: object = None) -> _FakeQuery:
        return _FakeQuery(self._documents)


class _FakeChildDocument:
    def __init__(self, documents: dict[str, list[_FakeDocument]]) -> None:
        self._documents = documents

    def collection(self, name: str) -> _FakeIntervalsCollection:
        return _FakeIntervalsCollection(self._documents)


class _FakeClient:
    def __init__(self, documents: dict[str, list[_FakeDocument]]) -> None:
        self._documents = documents

    def collection(self, name: str) -> "_FakeClient":
        return self

    def document(self, child_uid: str) -> _FakeChildDocument:
        return _FakeChildDocument(self._documents)


async def test_list_feed_intervals_returns_bottle_rows_without_amount(websession, monkeypatch) -> None:
    """Bottle feeds saved with no volume must still be listed.

    Payload copied from a real standalone row the app wrote on 2026-10-21
    (`mode`, `start`, `lastUpdated`, `offset`, `units`, `bottleType`, `tz`,
    no `amount`). The same shape is also covered inside a batch document.
    """
    standalone_row: dict[str, Any] = {
        "mode": "bottle",
        "start": 1792606727.167,
        "lastUpdated": 1792606727.34,
        "offset": -180.0,
        "units": "ml",
        "bottleType": "Breast Milk",
        "tz": "Europe/Vilnius",
    }
    batched_row: dict[str, Any] = {
        "mode": "bottle",
        "start": 1792606728.5,
        "lastUpdated": 1792606728.6,
        "offset": -180.0,
        "units": "ml",
        "bottleType": "Formula",
        "tz": "Europe/Vilnius",
    }
    documents = {
        "regular": [_FakeDocument(standalone_row)],
        "multi": [_FakeDocument({"multi": True, "data": {"1785513600-abc": batched_row}})],
    }

    api = HuckleberryAPI(email="test", password="test", timezone="UTC", websession=websession)
    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=_FakeClient(documents)))

    start_time = datetime.fromtimestamp(1792606000, tz=timezone.utc)
    end_time = datetime.fromtimestamp(1792607000, tz=timezone.utc)
    events = await api.list_feed_intervals("child", start_time, end_time)

    bottles = [event for event in events if isinstance(event, FirebaseBottleFeedIntervalData)]
    assert len(bottles) == 2
    assert all(bottle.amount is None for bottle in bottles)
    assert {bottle.bottleType for bottle in bottles} == {"Breast Milk", "Formula"}
