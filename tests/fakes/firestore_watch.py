"""Fake Firestore watch boundary for offline subscription tests.

The fakes mirror the real SDK shapes the adapter consumes: a client exposing
``collection(name).document(uid).on_snapshot(callback)``, a watch exposing
``is_active`` and ``unsubscribe()``, and callbacks receiving
``(documents, changes, read_time)`` where deletion is an empty document list
plus a ``REMOVED`` change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


class FakeDocumentSnapshot:
    """The subset of ``DocumentSnapshot`` the adapter reads."""

    def __init__(self, payload: dict[str, Any] | None) -> None:
        self._payload = payload
        self.exists = payload is not None

    def to_dict(self) -> dict[str, Any] | None:
        return None if self._payload is None else dict(self._payload)


class FakeChangeType:
    """Mirror of ``firestore_v1.watch.ChangeType``; ``.type`` is an enum."""

    def __init__(self, name: str) -> None:
        self.name = name


class FakeChange:
    """The subset of ``DocumentChange`` the adapter reads."""

    def __init__(self, change_type: str) -> None:
        self.type = FakeChangeType(change_type)


class FakeWatch:
    """A watch that can be driven by the test."""

    def __init__(self, callback: Callable[[Any, Any, Any], None]) -> None:
        self._callback = callback
        self.is_active = True
        self.close_count = 0
        self.close_delay = 0.0

    def push(
        self,
        payload: dict[str, Any] | None,
        *,
        deleted: bool = False,
        read_time: datetime | None = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        """Deliver one snapshot the way ``Watch.push`` does."""
        if deleted:
            documents: list[FakeDocumentSnapshot] = []
            changes = [FakeChange("REMOVED")]
        else:
            documents = [FakeDocumentSnapshot(raw if raw is not None else payload)]
            changes = [FakeChange("MODIFIED")]
        self._callback(documents, changes, read_time or datetime.now(timezone.utc))

    def die(self) -> None:
        """Terminal, non-recoverable failure: inactive without a requested close."""
        self.is_active = False

    def unsubscribe(self) -> None:
        self.close_count += 1
        self.is_active = False

    def close(self) -> None:
        self.unsubscribe()


class FakeDocumentReference:
    def __init__(self, client: "FakeFirestoreClient", collection: str, child_uid: str) -> None:
        self._client = client
        self.collection_name = collection
        self.child_uid = child_uid

    def on_snapshot(self, callback: Callable[[Any, Any, Any], None]) -> FakeWatch:
        watch = FakeWatch(callback)
        self._client.watches.append(watch)
        self._client._by_doc[(self.collection_name, self.child_uid)] = watch
        return watch


class FakeCollection:
    def __init__(self, client: "FakeFirestoreClient", name: str) -> None:
        self._client = client
        self.name = name
        self._documents: dict[str, FakeDocumentReference] = {}

    def document(self, child_uid: str) -> FakeDocumentReference:
        if child_uid not in self._documents:
            self._documents[child_uid] = FakeDocumentReference(self._client, self.name, child_uid)
        return self._documents[child_uid]


class _FakeTransport:
    def __init__(self, client: "FakeFirestoreClient") -> None:
        self._client = client

    def close(self) -> None:
        self._client.closed = True


class _FakeFirestoreApi:
    def __init__(self, client: "FakeFirestoreClient") -> None:
        self.transport = _FakeTransport(client)


class FakeFirestoreClient:
    """Stand-in for the sync ``firestore.Client`` used by watches.

    ``Client`` has no ``close()``; the transport under ``_firestore_api`` is the
    real release path, so the fake mirrors that.
    """

    def __init__(self, token: str | None = None) -> None:
        self.token = token
        self.closed = False
        self.watches: list[FakeWatch] = []
        self._by_doc: dict[tuple[str, str], FakeWatch] = {}
        self._collections: dict[str, FakeCollection] = {}
        self._firestore_api = _FakeFirestoreApi(self)

    def collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection(self, name)
        return self._collections[name]

    def watch(self, collection: str, child_uid: str) -> FakeWatch:
        """The single watch currently attached to a document."""
        return self._by_doc[(collection, child_uid)]
