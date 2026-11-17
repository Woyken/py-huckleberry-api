"""Async subscriptions over Firestore document watches.

Firestore's document watch is a hot, replay-1, non-terminating stream with no
backpressure and no error channel.  This module turns it into an asyncio
primitive:

* :class:`Snapshot` is the unit of delivery: a value plus ``exists``, ``as_of``,
  a per-receipt ``seq`` and a sticky ``resync`` flag.
* :class:`Subscription` is a conflated async iterable and async context manager
  over one document.  It yields the newest snapshot since the last
  ``__anext__`` and never buffers a backlog.
* :class:`WatchFailed` is the terminal error raised from ``__anext__`` and
  ``ready()`` when a watch dies for good.

The sync ``firestore.Client`` and its worker thread remain: the Python SDK has
no async watch.  One client is shared by every handle, one handle is shared by
every consumer of the same document, and the callback shim for the deprecated
``setup_*_listener`` API is a passthrough sink on that same handle.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import ValidationError

_LOGGER = logging.getLogger("huckleberry_api.api")

__all__ = ["Snapshot", "Subscription", "WatchFailed"]

T = TypeVar("T")


class WatchFailed(Exception):
    """A document watch failed terminally and will not deliver again."""


@dataclass(frozen=True)
class Snapshot(Generic[T]):
    """One delivered state of a watched document."""

    value: T | None
    exists: bool
    as_of: datetime
    seq: int
    resync: bool


@dataclass(frozen=True)
class _Failed:
    error: Exception


class _Stopped:
    """Sentinel delivered when a subscription was stopped deliberately."""


def _as_utc(value: datetime | None) -> datetime:
    """Normalise Firestore's ``read_time`` to aware UTC without converting it."""
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class _Latest(Generic[T]):
    """A one-slot conflated mailbox: newest value wins, no backlog."""

    def __init__(self) -> None:
        self._value: T | _Failed | _Stopped = _Stopped()
        self._event = asyncio.Event()
        self._terminal = False

    def offer(self, value: T | _Failed | _Stopped) -> None:
        self._value = value
        self._terminal = isinstance(value, (_Failed, _Stopped))
        self._event.set()

    async def take(self) -> T:
        if not self._terminal or self._event.is_set():
            await self._event.wait()
            self._event.clear()
        value = self._value
        if isinstance(value, _Failed):
            raise value.error
        if isinstance(value, _Stopped):
            raise StopAsyncIteration
        return value


class _Handle(Generic[T]):
    """One Firestore watch per document, fanned out to subscribers and sinks."""

    def __init__(
        self,
        owner: "_WatchHub",
        collection: str,
        child_uid: str,
        parser: Callable[[dict[str, Any]], T],
    ) -> None:
        self._owner = owner
        self._collection = collection
        self._child_uid = child_uid
        self._parser = parser
        self._loop = asyncio.get_running_loop()
        self._subscribers: set[Subscription[T]] = set()
        self._sinks: list[Callable[[Snapshot[T]], None]] = []
        self._watch: Any = None
        self._seq = 0
        self._reconnects = 0
        self._last: Snapshot[T] | None = None
        self._error: WatchFailed | None = None
        self._closing = False
        self._requested_close = False

    # -- registration -----------------------------------------------------

    @property
    def refcount(self) -> int:
        return len(self._subscribers) + len(self._sinks)

    def attach(self, subscription: "Subscription[T]") -> None:
        if self._error is not None:
            raise self._error
        self._subscribers.add(subscription)
        try:
            if self._last is not None:
                subscription._seed(self._last)
            if self._watch is None:
                self._open()
        except Exception:
            self._subscribers.discard(subscription)
            raise

    def detach(self, subscription: "Subscription[T]") -> "asyncio.Task[None] | None":
        self._subscribers.discard(subscription)
        return self._close_if_idle()

    def add_sink(self, sink: Callable[[Snapshot[T]], None]) -> None:
        if self._error is not None:
            raise self._error
        self._sinks.append(sink)
        try:
            if self._watch is None:
                self._open()
        except Exception:
            self._sinks.remove(sink)
            raise

    def remove_sink(self, sink: Callable[[Snapshot[T]], None]) -> None:
        try:
            self._sinks.remove(sink)
        except ValueError:
            return
        self._close_if_idle()

    # -- transport --------------------------------------------------------

    def _document(self) -> Any:
        return self._owner.document(self._collection, self._child_uid)

    def _open(self) -> None:
        self._requested_close = False
        self._closing = False
        self._watch = self._document().on_snapshot(self._on_snapshot)
        _LOGGER.debug("Watch opened for %s/%s", self._collection, self._child_uid)

    def _on_snapshot(self, documents: Any, changes: Any, read_time: Any) -> None:
        """Runs on the Firestore watch thread."""
        if self._closing:
            return
        try:
            snapshot = self._build(documents, changes, read_time)
        except ValidationError as err:
            # One schema drift must not kill the watch for every consumer.
            _LOGGER.warning(
                "Skipping malformed %s/%s payload: %s", self._collection, self._child_uid, err
            )
            return
        except Exception:  # noqa: BLE001 - never let an exception reach Watch.push
            _LOGGER.exception("Failed to build snapshot for %s/%s", self._collection, self._child_uid)
            return

        self._seq += 1
        snapshot = replace(snapshot, seq=self._seq)
        self._last = snapshot

        for sink in list(self._sinks):
            self._invoke_sink(sink, snapshot)

        for subscription in list(self._subscribers):
            try:
                self._loop.call_soon_threadsafe(subscription._offer, snapshot)
            except RuntimeError:
                # The consumer's loop is closed; nothing can be delivered to it.
                _LOGGER.debug("Dropping subscriber for %s/%s: loop closed", self._collection, self._child_uid)
                self._subscribers.discard(subscription)
        if self.refcount == 0:
            # Clean up from the hub loop, never from the watch thread.
            self._owner.reclaim(self)

    def _invoke_sink(self, sink: Callable[[Snapshot[T]], None], snapshot: Snapshot[T]) -> None:
        try:
            sink(snapshot)
        except Exception:  # noqa: BLE001 - legacy callbacks must not kill the watch
            _LOGGER.exception("Listener callback failed for %s/%s", self._collection, self._child_uid)

    def _build(self, documents: Any, changes: Any, read_time: Any) -> Snapshot[T]:
        as_of = _as_utc(read_time)
        documents = list(documents or ())
        if documents:
            document = documents[0]
            if document.exists:
                payload = document.to_dict() or {}
                return Snapshot(
                    value=self._parser(payload),
                    exists=True,
                    as_of=as_of,
                    seq=0,
                    resync=False,
                )
        removed = any(_is_removed(change) for change in (changes or ()))
        if removed or not documents:
            # Deletion, or an initially absent document: a state, not a
            # termination.  The SDK sends an empty document list for both.
            return Snapshot(value=None, exists=False, as_of=as_of, seq=0, resync=False)
        return Snapshot(value=None, exists=False, as_of=as_of, seq=0, resync=False)

    # -- lifecycle --------------------------------------------------------

    def _close_if_idle(self) -> "asyncio.Task[None] | None":
        if self.refcount:
            return None
        self._owner.forget(self)
        task = self._close()
        self._owner.maybe_close_client()
        return task

    def _close(self) -> "asyncio.Task[None] | None":
        watch = self._watch
        self._watch = None
        self._closing = True
        self._requested_close = True
        if watch is None:
            return None
        return self._owner.close_watch(watch)

    def _fail(self, error: WatchFailed) -> None:
        if self._error is not None:
            return
        self._error = error
        _LOGGER.error("Watch failed for %s/%s: %s", self._collection, self._child_uid, error)
        for subscription in list(self._subscribers):
            try:
                self._loop.call_soon_threadsafe(subscription._offer, _Failed(error))
            except RuntimeError:
                pass
        if self._watch is not None:
            watch = self._watch
            self._watch = None
            self._owner.close_watch(watch)

    def _stop(self) -> None:
        for subscription in list(self._subscribers):
            try:
                self._loop.call_soon_threadsafe(subscription._offer, _Stopped())
            except RuntimeError:
                pass
        self._subscribers.clear()
        self._sinks.clear()
        self._close()

    def _rotate(self) -> None:
        self._reconnects += 1
        self._closing = True
        if self._watch is not None:
            watch = self._watch
            self._watch = None
            self._owner.close_watch(watch)
        self._open()
        for subscription in list(self._subscribers):
            subscription._request_resync()

    def reconnect(self) -> None:
        self._rotate()

    def check_health(self) -> None:
        watch = self._watch
        if watch is None or self._requested_close or self._closing:
            return
        if getattr(watch, "is_active", True) is False:
            self._fail(WatchFailed(f"Watch for {self._collection}/{self._child_uid} stopped"))


class _WatchHub:
    """Owns the sync client, one handle per document, and rotation."""

    def __init__(self, client_factory: Callable[[str], Any]) -> None:
        self._client_factory = client_factory
        self._client: Any = None
        self._token: str | None = None
        self._handles: dict[tuple[str, str], _Handle[Any]] = {}
        self._lock = asyncio.Lock()
        self._loop = asyncio.get_running_loop()
        self._close_tasks: set[asyncio.Task[None]] = set()
        self._watchdog: asyncio.Task[None] | None = None

    async def ensure_client(self, token: str) -> None:
        if self._client is not None and self._token == token:
            return
        async with self._lock:
            if self._client is not None and self._token == token:
                return
            old = self._client
            self._client = self._client_factory(token)
            self._token = token
            if old is not None:
                await self._close_client(old)

    def document(self, collection: str, child_uid: str) -> Any:
        if self._client is None:
            raise RuntimeError("Watch client is not initialised")
        return self._client.collection(collection).document(child_uid)

    def handle(
        self,
        collection: str,
        child_uid: str,
        parser: Callable[[dict[str, Any]], Any],
        *,
        for_subscription: bool = True,
    ) -> _Handle[Any]:
        if self._loop is not asyncio.get_running_loop():
            if not self._loop.is_closed():
                if for_subscription:
                    raise RuntimeError(
                        "Watch hub is bound to a different running event loop; "
                        "create one HuckleberryAPI per loop"
                    )
                # Sinks have no loop affinity and need no delivery loop, so a
                # sink-only registration from another live loop is fine.
            else:
                # The previous loop is gone: drop its subscriptions, keep handles
                # that sinks still reference, and rebind delivery.
                for handle in list(self._handles.values()):
                    for subscription in list(handle._subscribers):
                        handle._subscribers.discard(subscription)
                    if handle.refcount == 0:
                        self.forget(handle)
                        handle._close()
                self._loop = asyncio.get_running_loop()
                self._lock = asyncio.Lock()
        key = (collection, child_uid)
        handle = self._handles.get(key)
        if handle is not None and handle._error is not None:
            # Never join a dead handle: a new consumer starts a fresh watch.
            self.forget(handle)
            handle._close()
            handle = None
        if handle is None:
            handle = _Handle(self, collection, child_uid, parser)
            self._handles[key] = handle
        else:
            handle._loop = self._loop
        self._ensure_watchdog()
        return handle

    def forget(self, handle: _Handle[Any]) -> None:
        key = (handle._collection, handle._child_uid)
        if self._handles.get(key) is handle:
            del self._handles[key]

    async def rotate(self, token: str) -> None:
        await self.ensure_client(token)
        for handle in list(self._handles.values()):
            handle._rotate()

    def close_watch(self, watch: Any) -> "asyncio.Task[None] | None":
        """Close a watch off the event loop; the SDK joins its thread for ~1s."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _close_watch_sync(watch)
            return None
        task = loop.create_task(asyncio.to_thread(_close_watch_sync, watch))
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)
        task.add_done_callback(_log_close_failure)
        return task

    async def _close_client(self, client: Any) -> None:
        try:
            await asyncio.shield(asyncio.to_thread(_close_watch_client, client))
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Error closing watch client", exc_info=True)

    def maybe_close_client(self) -> None:
        """Release the shared client once no handle needs it."""
        if self._handles or self._client is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Called from a worker thread; a later rotate/stop releases it.
            return
        client = self._client
        self._client = None
        self._token = None
        task = loop.create_task(asyncio.to_thread(_close_watch_client, client))
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)
        task.add_done_callback(_log_close_failure)

    def reclaim(self, handle: _Handle[Any]) -> None:
        """Close an orphaned handle from the hub loop, if that loop still runs."""
        try:
            if self._loop.is_closed():
                return
            self._loop.call_soon_threadsafe(handle._close_if_idle)
        except RuntimeError:
            return

    async def stop(self) -> None:
        watchdog = self._watchdog
        self._watchdog = None
        if watchdog is not None:
            watchdog.cancel()
        for handle in list(self._handles.values()):
            handle._stop()
        self._handles.clear()
        client = self._client
        self._client = None
        self._token = None
        if client is not None:
            await self._close_client(client)

    def _ensure_watchdog(self) -> None:
        if self._watchdog is None or self._watchdog.done():
            self._watchdog = asyncio.get_running_loop().create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(5.0)
            for handle in list(self._handles.values()):
                try:
                    handle.check_health()
                except Exception:  # noqa: BLE001 - a watchdog must never die
                    _LOGGER.debug("Watchdog check failed", exc_info=True)

    def check_health(self) -> None:
        for handle in list(self._handles.values()):
            handle.check_health()


def _close_watch_sync(watch: Any) -> None:
    for name in ("unsubscribe", "close"):
        method = getattr(watch, name, None)
        if callable(method):
            method()
            return


def _close_watch_client(client: Any) -> None:
    """Release the sync client's transport; ``Client`` has no ``close()``."""
    transport = getattr(getattr(client, "_firestore_api", None), "transport", None)
    close = getattr(transport, "close", None)
    if callable(close):
        close()
        return
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _is_removed(change: Any) -> bool:
    """``DocumentChange.type`` is a ``ChangeType`` enum, not a string."""
    change_type = getattr(change, "type", None)
    return getattr(change_type, "name", change_type) == "REMOVED"


def _log_close_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        _LOGGER.debug("Error closing watch", exc_info=error)


class Subscription(Generic[T], AsyncIterator[Snapshot[T]]):
    """Conflated async iterator over one document.

    ``watch_*()`` returns one of these without doing I/O.  Authentication,
    registration and watch creation happen on first use: ``__aenter__``, the
    first ``__anext__``, ``ready()`` or ``reconnect()``.
    """

    def __init__(
        self,
        owner: Any,
        collection: str,
        child_uid: str,
        parser: Callable[[dict[str, Any]], T],
    ) -> None:
        self._owner = owner
        self._collection = collection
        self._child_uid = child_uid
        self._parser = parser
        self._handle: _Handle[T] | None = None
        self._slot: _Latest[Snapshot[T]] = _Latest()
        self._resync_pending = True
        self._closed = False
        self._reconnects = 0

    # -- public surface ---------------------------------------------------

    @property
    def latest(self) -> Snapshot[T] | None:
        return None if self._handle is None else self._handle._last

    @property
    def reconnects(self) -> int:
        return 0 if self._handle is None else self._handle._reconnects

    async def __aenter__(self) -> "Subscription[T]":
        if self._closed:
            raise RuntimeError("Subscription is closed")
        await self._ensure_started()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    def __aiter__(self) -> "Subscription[T]":
        return self

    async def __anext__(self) -> Snapshot[T]:
        if self._closed:
            raise StopAsyncIteration
        await self._ensure_started()
        return await self._next()

    async def ready(self, timeout: float | None = None) -> Snapshot[T]:
        """Return the current or next snapshot.

        Registers the subscription if this is the first call.
        """
        if self._closed:
            raise StopAsyncIteration
        await self._ensure_started()
        if timeout is None:
            return await self._next()
        try:
            async with asyncio.timeout(timeout):
                return await self._next()
        except TimeoutError:
            raise TimeoutError(
                f"No snapshot for {self._collection}/{self._child_uid} within {timeout}s"
            ) from None

    async def reconnect(self) -> None:
        """Reopen this document's watch, resyncing every subscription on it.

        Safe after a failure or a global stop: it re-resolves the handle, so a
        dead or stopped transport is replaced instead of being reopened in place.
        """
        if self._closed:
            raise StopAsyncIteration
        if self._handle is not None:
            self._handle.detach(self)
            self._handle = None
        await self._ensure_started()
        assert self._handle is not None
        self._handle.reconnect()
        self._request_resync()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._handle is None:
            return
        handle = self._handle
        self._handle = None
        task = handle.detach(self)
        if task is not None:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - closing must not mask the exit
                _LOGGER.debug("Error closing %s/%s watch", self._collection, self._child_uid, exc_info=True)

    # -- internals --------------------------------------------------------

    async def _ensure_started(self) -> None:
        if self._handle is not None:
            return
        if self._closed:
            raise StopAsyncIteration
        await self._owner._ensure_authenticated()
        handle = await self._owner._watch_handle(self._collection, self._child_uid, self._parser)
        handle.attach(self)
        self._handle = handle

    async def _next(self) -> Snapshot[T]:
        snapshot = await self._slot.take()
        # A newer receipt may have landed after this slot value was queued;
        # conflation promises the newest state, so prefer it.
        if self._handle is not None and self._handle._last is not None:
            latest = self._handle._last
            if latest.seq > snapshot.seq:
                snapshot = latest
        if self._resync_pending:
            self._resync_pending = False
            snapshot = replace(snapshot, resync=True)
        return snapshot

    def _offer(self, snapshot: Snapshot[T]) -> None:
        self._slot.offer(snapshot)

    def _seed(self, snapshot: Snapshot[T]) -> None:
        self._slot.offer(snapshot)

    def _request_resync(self) -> None:
        self._resync_pending = True
