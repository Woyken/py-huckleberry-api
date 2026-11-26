"""Offline tests for async document subscriptions.

These drive the fake Firestore boundary in ``tests/fakes/firestore_watch.py``,
so they exercise the real adapter, hub and subscription without credentials.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest

from huckleberry_api import HuckleberryAPI
from huckleberry_api.firebase_types import FirebaseSleepDocumentData
from huckleberry_api.subscriptions import Snapshot
from huckleberry_api.subscriptions import Subscription
from huckleberry_api.subscriptions import WatchFailed
from huckleberry_api.subscriptions import _WatchHub

from .fakes.firestore_watch import FakeDocumentSnapshot
from .fakes.firestore_watch import FakeFirestoreClient

PARSE_SLEEP = FirebaseSleepDocumentData.model_validate


def payload(uuid: str = "session-1", active: bool = True) -> dict[str, Any]:
    return {"timer": {"active": active, "paused": False, "uuid": uuid}}


async def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def timer_uuid(snapshot: Snapshot[Any]) -> str:
    """Assert a snapshot carries a sleep timer and return its session uuid."""
    assert snapshot.value is not None
    assert snapshot.value.timer is not None
    return snapshot.value.timer.uuid


class DummyOwner:
    """Minimal stand-in for HuckleberryAPI's subscription plumbing."""

    def __init__(self) -> None:
        self.clients: list[FakeFirestoreClient] = []
        self.hub = _WatchHub(self._factory)

    def _factory(self, token: str) -> FakeFirestoreClient:
        client = FakeFirestoreClient(token)
        self.clients.append(client)
        return client

    @property
    def client(self) -> FakeFirestoreClient:
        return self.clients[-1]

    async def _ensure_authenticated(self) -> None:
        return None

    async def _watch_handle(self, collection: str, child_uid: str, parser: Any) -> Any:
        self.hub.ensure_client("token")
        return self.hub.handle(collection, child_uid, parser)


async def started(owner: DummyOwner) -> Subscription[Any]:
    subscription: Subscription[Any] = Subscription(owner, "sleep", "child", PARSE_SLEEP)
    await subscription.__aenter__()
    return subscription


def make_api() -> HuckleberryAPI:
    """An API instance whose watch transport is a fake and auth is a no-op."""
    api = HuckleberryAPI(
        email="test@example.com",
        password="password",
        timezone="UTC",
        websession=SimpleNamespace(),  # type: ignore[arg-type]
    )
    api.id_token = "token"
    api._create_watch_client = lambda token: FakeFirestoreClient(token)  # type: ignore[method-assign]

    async def no_auth() -> None:
        return None

    api._ensure_authenticated = no_auth  # type: ignore[method-assign]
    return api


async def test_conflated_newest_wins() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")

    watch.push(payload("first"))
    watch.push(payload("second"))

    snapshot = await sub.ready()
    assert snapshot.value is not None
    assert timer_uuid(snapshot) == "second"


async def test_sink_receives_every_snapshot_in_order() -> None:
    owner = DummyOwner()
    handle = await owner._watch_handle("sleep", "child", PARSE_SLEEP)
    seen: list[str] = []
    handle.add_sink(lambda snapshot: seen.append(timer_uuid(snapshot)))

    watch = owner.client.watch("sleep", "child")
    watch.push(payload("one"))
    watch.push(payload("two"))
    watch.push(payload("three"))

    assert seen == ["one", "two", "three"]


async def test_listener_registration_warns_deprecated() -> None:
    api = make_api()
    with pytest.warns(DeprecationWarning):
        await api.setup_sleep_listener("child", lambda data: None)


async def test_deletion_is_a_state_and_skips_callbacks() -> None:
    api = make_api()
    calls: list[Any] = []
    await api.setup_sleep_listener("child", calls.append)

    sub = api.watch_sleep("child")
    await sub.__aenter__()

    client = watch_client(api)
    client.watch("sleep", "child").push(None, deleted=True)

    snapshot = await sub.ready()
    assert snapshot.exists is False
    assert snapshot.value is None
    assert calls == []


async def test_malformed_payload_is_skipped_and_recovery_works() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")

    watch.push(None, raw={"timer": {"active": "not-a-bool", "paused": False, "uuid": "bad"}})
    watch.push(payload("good"))

    snapshot = await sub.ready()
    assert timer_uuid(snapshot) == "good"


async def test_active_but_silent_watch_does_not_fail() -> None:
    owner = DummyOwner()
    sub = await started(owner)

    owner.hub.check_health()
    await asyncio.sleep(0)

    with pytest.raises(TimeoutError):
        await sub.ready(timeout=0.05)


async def test_terminal_watch_death_raises_watch_failed() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")

    watch.die()
    owner.hub.check_health()
    await asyncio.sleep(0)

    with pytest.raises(WatchFailed):
        await sub.ready()


async def test_ready_timeout_leaves_subscription_usable() -> None:
    owner = DummyOwner()
    sub = await started(owner)

    with pytest.raises(TimeoutError):
        await sub.ready(timeout=0.01)

    owner.client.watch("sleep", "child").push(payload("later"))
    assert timer_uuid(await sub.ready(timeout=1)) == "later"


async def test_ready_registers_a_lazy_subscription() -> None:
    owner = DummyOwner()
    sub: Subscription[Any] = Subscription(owner, "sleep", "child", PARSE_SLEEP)

    with pytest.raises(TimeoutError):
        await sub.ready(timeout=0.01)

    assert owner.clients, "ready() must have started the transport"
    assert owner.client.watches, "ready() must have created the watch"


async def test_late_joiner_is_seeded_with_resync() -> None:
    owner = DummyOwner()
    sub1 = await started(owner)
    owner.client.watch("sleep", "child").push(payload("first"))
    first = await sub1.ready()

    sub2 = await started(owner)
    second = await sub2.ready()

    assert second.seq == first.seq
    assert second.resync is True
    assert timer_uuid(second) == "first"


async def test_late_joiner_resync_survives_a_race() -> None:
    owner = DummyOwner()
    sub1 = await started(owner)
    owner.client.watch("sleep", "child").push(payload("first"))
    await sub1.ready()

    sub2 = await started(owner)
    owner.client.watch("sleep", "child").push(payload("second"))

    snapshot = await sub2.__anext__()
    assert snapshot.resync is True
    assert timer_uuid(snapshot) == "second"


async def test_seq_reports_conflated_gaps() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")

    watch.push(payload("one"))
    first = await sub.__anext__()

    watch.push(payload("two"))
    watch.push(payload("three"))
    second = await sub.__anext__()

    assert second.seq - first.seq == 2


async def test_as_of_is_aware_utc() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    read_time = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)

    owner.client.watch("sleep", "child").push(payload(), read_time=read_time)
    snapshot = await sub.ready()

    assert snapshot.as_of.utcoffset() == timedelta(0)
    assert snapshot.as_of == read_time


async def test_rotation_keeps_one_watch_and_resyncs_every_subscriber() -> None:
    owner = DummyOwner()
    sub1 = await started(owner)
    sub2 = await started(owner)
    owner.client.watch("sleep", "child").push(payload("before"))
    await sub1.ready()
    await sub2.ready()

    await owner.hub.rotate("fresh-token")

    assert len(owner.client.watches) == 1
    await wait_until(lambda: owner.clients[0].closed)
    assert sub1.reconnects == sub2.reconnects == 1
    owner.client.watch("sleep", "child").push(payload("after"))
    assert (await sub1.ready(timeout=1)).resync is True
    assert (await sub2.ready(timeout=1)).resync is True


async def test_reconnect_is_document_wide() -> None:
    owner = DummyOwner()
    sub1 = await started(owner)
    sub2 = await started(owner)
    owner.client.watch("sleep", "child").push(payload("one"))
    await sub1.ready()
    await sub2.ready()

    await sub1.reconnect()

    assert sub1.reconnects == sub2.reconnects == 1
    owner.client.watch("sleep", "child").push(payload("two"))
    assert (await sub2.ready(timeout=1)).resync is True


async def test_sink_only_handle_survives_subscription_exit() -> None:
    owner = DummyOwner()
    handle = await owner._watch_handle("sleep", "child", PARSE_SLEEP)
    handle.add_sink(lambda snapshot: None)

    async with Subscription(owner, "sleep", "child", PARSE_SLEEP):
        pass

    assert ("sleep", "child") in owner.hub._handles
    assert owner.client.watches[-1].close_count == 0


async def test_anext_first_use_registers_the_watch() -> None:
    owner = DummyOwner()
    sub: Subscription[Any] = Subscription(owner, "sleep", "child", PARSE_SLEEP)

    task = asyncio.create_task(sub.__anext__())
    await asyncio.sleep(0)
    owner.client.watch("sleep", "child").push(payload("iterated"))

    snapshot = await asyncio.wait_for(task, timeout=1)
    assert timer_uuid(snapshot) == "iterated"


async def test_leaving_context_closes_the_watch_once() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")
    client = owner.client

    await sub.aclose()
    for _ in range(50):
        if watch.close_count and client.closed:
            break
        await asyncio.sleep(0.02)

    assert watch.close_count == 1
    assert client.closed is True
    assert ("sleep", "child") not in owner.hub._handles


async def test_client_survives_while_another_document_is_watched() -> None:
    owner = DummyOwner()
    sleep_sub = await started(owner)
    feed_sub: Subscription[Any] = Subscription(owner, "feed", "child", PARSE_SLEEP)
    await feed_sub.__aenter__()

    client = owner.client
    await sleep_sub.aclose()
    await asyncio.sleep(0.05)

    assert client.closed is False
    client.watch("feed", "child").push(payload("still-live"))
    assert timer_uuid(await feed_sub.ready()) == "still-live"


async def test_reconnect_recovers_after_terminal_failure() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")

    watch.die()
    owner.hub.check_health()
    await asyncio.sleep(0)
    with pytest.raises(WatchFailed):
        await sub.ready()

    await sub.reconnect()

    owner.client.watch("sleep", "child").push(payload("recovered"))
    snapshot = await sub.ready(timeout=1)
    assert timer_uuid(snapshot) == "recovered"


async def test_offer_from_a_replaced_generation_is_ignored() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")
    watch.push(payload("one"))
    await sub.ready(timeout=1)

    handle = sub._handle
    assert handle is not None
    stale_generation = handle._generation
    await sub.reconnect()

    # A snapshot queued by the pre-rotation watch must not surface.
    sub._offer(
        Snapshot(value=None, exists=False, as_of=datetime.now(timezone.utc), seq=999, resync=True),
        handle,
        stale_generation,
    )
    await asyncio.sleep(0)

    assert sub.latest is not None
    assert sub.latest.seq != 999
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(sub.__anext__(), timeout=0.05)


async def test_offer_from_a_replaced_handle_is_ignored() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    handle = sub._handle
    assert handle is not None

    owner.client.watch("sleep", "child").die()
    owner.hub.check_health()
    await asyncio.sleep(0)
    with pytest.raises(WatchFailed):
        await sub.ready()

    await sub.reconnect()
    assert sub._handle is not handle

    # Same generation number, different handle: identity must reject it.
    sub._offer(
        Snapshot(value=None, exists=False, as_of=datetime.now(timezone.utc), seq=999, resync=True),
        handle,
        handle._generation,
    )

    with pytest.raises(TimeoutError):
        await sub.ready(timeout=0.05)


async def test_ready_reports_current_state_repeatedly() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")

    watch.push(payload("one"))
    assert timer_uuid(await sub.ready(timeout=1)) == "one"
    assert timer_uuid(await sub.ready(timeout=1)) == "one"

    watch.push(payload("two"))
    assert timer_uuid(await sub.ready(timeout=1)) == "two"


async def test_queued_duplicate_is_not_redelivered() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    owner.client.watch("sleep", "child").push(payload("one"))

    delivered = await sub.__anext__()
    handle = sub._handle
    assert handle is not None
    sub._offer(delivered, handle, handle._generation)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(sub.__anext__(), timeout=0.05)


async def test_aclose_wakes_a_pending_read() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    waiter = asyncio.create_task(sub.ready())
    await asyncio.sleep(0)

    await sub.aclose()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(waiter, timeout=1)


async def test_rotate_skips_receipts_from_the_replaced_watch() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    handle = sub._handle
    assert handle is not None
    stale_generation = handle._generation

    await sub.reconnect()
    handle._on_snapshot(
        stale_generation,
        [FakeDocumentSnapshot(payload("stale"))],
        [],
        datetime.now(timezone.utc),
    )

    with pytest.raises(TimeoutError):
        await sub.ready(timeout=0.05)


class SlowAuthOwner(DummyOwner):
    """Owner whose authentication pauses, to race startup against aclose."""

    def __init__(self) -> None:
        super().__init__()
        self.auth_entered = asyncio.Event()
        self.auth_release = asyncio.Event()

    async def _ensure_authenticated(self) -> None:
        self.auth_entered.set()
        await self.auth_release.wait()


async def test_close_during_startup_does_not_attach() -> None:
    owner = SlowAuthOwner()
    sub: Subscription[Any] = Subscription(owner, "sleep", "child", PARSE_SLEEP)

    task = asyncio.create_task(sub.__aenter__())
    await owner.auth_entered.wait()
    await sub.aclose()
    owner.auth_release.set()

    with pytest.raises(RuntimeError):
        await asyncio.wait_for(task, timeout=1)

    assert sub._handle is None
    assert owner.hub._handles == {}


async def test_ready_reports_stop_after_a_delivery() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    owner.client.watch("sleep", "child").push(payload("one"))
    await sub.ready()

    await owner.hub.stop()

    with pytest.raises(StopAsyncIteration):
        await sub.ready()


async def test_ready_reports_failure_after_a_delivery() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")
    watch.push(payload("one"))
    await sub.ready()

    watch.die()
    owner.hub.check_health()
    await asyncio.sleep(0)

    with pytest.raises(WatchFailed):
        await sub.ready()


async def test_commit_rejects_a_stale_generation() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    handle = sub._handle
    assert handle is not None
    owner.client.watch("sleep", "child").push(payload("before"))
    await sub.ready()
    stale_generation = handle._generation
    previous = handle._last

    await sub.reconnect()

    stale = Snapshot(value=None, exists=False, as_of=datetime.now(timezone.utc), seq=999, resync=True)
    assert handle._commit(stale_generation, stale) is None
    assert handle._last is previous


async def test_ready_does_not_reuse_a_pre_rotation_snapshot() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    owner.client.watch("sleep", "child").push(payload("before"))
    await sub.ready()

    await owner.hub.rotate("fresh-token")

    # The old receipt must not be reported as a resync result.
    with pytest.raises(TimeoutError):
        await sub.ready(timeout=0.05)

    owner.client.watch("sleep", "child").push(payload("after"))
    snapshot = await sub.ready(timeout=1)
    assert snapshot.resync is True
    assert timer_uuid(snapshot) == "after"


async def test_mailbox_reset_does_not_end_iteration() -> None:
    owner = DummyOwner()
    sub = await started(owner)

    def snapshot(seq: int) -> Snapshot[Any]:
        return Snapshot(value=None, exists=False, as_of=datetime.now(timezone.utc), seq=seq, resync=False)

    task = asyncio.create_task(sub.__anext__())
    await asyncio.sleep(0)

    # Race: the reader wakes on receipt 1, then a reconnect resets the mailbox
    # before it reads. That must wait for the new generation, not raise.
    sub._slot.offer(snapshot(1))
    sub._slot.reset()
    await asyncio.sleep(0.05)
    assert not task.done(), "mailbox reset must not end iteration"

    sub._slot.offer(snapshot(2))
    assert (await asyncio.wait_for(task, timeout=1)).seq == 2


async def test_legacy_sinks_receive_independent_values() -> None:
    api = make_api()
    seen: list[str | None] = []

    def mutate(data: FirebaseSleepDocumentData) -> None:
        assert data.timer is not None
        data.timer.uuid = "mutated"

    def observe(data: FirebaseSleepDocumentData) -> None:
        seen.append(data.timer.uuid if data.timer else None)

    await api.setup_sleep_listener("child", mutate)
    await api.setup_sleep_listener("child", observe)

    watch_client(api).watch("sleep", "child").push(payload("original"))

    assert seen == ["original"]


class _FakeAuthResponse:
    status = 200
    reason = "OK"
    headers: dict[str, str] = {}

    async def __aenter__(self) -> "_FakeAuthResponse":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def json(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        return {
            "idToken": "fresh-token",
            "refreshToken": "fresh-refresh",
            "localId": "user-1",
            "expiresIn": "3600",
        }


async def test_authenticate_rotates_live_watches() -> None:
    api = make_api()
    api.websession = SimpleNamespace(post=lambda *args, **kwargs: _FakeAuthResponse())

    rotated: list[str] = []

    async def hook() -> None:
        rotated.append(api.id_token or "")

    api._on_credentials_refreshed = cast(Any, hook)
    api._firestore_client = cast(Any, object())

    await api.authenticate()

    assert rotated == ["fresh-token"]
    assert api._firestore_client is None


async def test_reconnect_failure_keeps_terminal_state() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")

    watch.die()
    owner.hub.check_health()
    await asyncio.sleep(0)
    with pytest.raises(WatchFailed):
        await sub.ready()

    owner.client.fail_on_snapshot = True
    with pytest.raises(RuntimeError):
        await sub.reconnect()

    # The failed re-open must not clear the terminal state into a silent hang.
    with pytest.raises(WatchFailed):
        await sub.ready(timeout=0.05)


async def test_reconnect_recovers_after_global_stop() -> None:
    owner = DummyOwner()
    sub = await started(owner)

    await owner.hub.stop()
    with pytest.raises(StopAsyncIteration):
        await sub.ready()

    await sub.reconnect()

    owner.client.watch("sleep", "child").push(payload("after-stop"))
    snapshot = await sub.ready(timeout=1)
    assert timer_uuid(snapshot) == "after-stop"


async def test_cancelling_context_exit_still_closes_once() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")
    watch.close_delay = 0.2

    async def consume() -> None:
        async with sub:
            await asyncio.sleep(10)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(100):
        if watch.close_count:
            break
        await asyncio.sleep(0.02)
    assert watch.close_count == 1


async def test_reconnect_keeps_seq_reconnects_and_client() -> None:
    owner = DummyOwner()
    sub = await started(owner)
    watch = owner.client.watch("sleep", "child")
    client = owner.client
    watch.push(payload("one"))
    first = await sub.ready()

    await sub.reconnect()

    assert sub.reconnects == 1
    assert owner.client is client
    reopened = client.watch("sleep", "child")
    assert reopened is not watch
    reopened.push(payload("two"))
    second = await sub.ready()
    assert second.seq > first.seq
    assert second.resync is True


async def test_stop_all_ends_pending_waiters_and_reopens_cleanly() -> None:
    owner = DummyOwner()
    sub = await started(owner)

    await owner.hub.stop()

    with pytest.raises(StopAsyncIteration):
        await sub.ready()

    fresh: Subscription[Any] = Subscription(owner, "sleep", "child", PARSE_SLEEP)
    await fresh.__aenter__()
    owner.client.watch("sleep", "child").push(payload("again"))
    assert timer_uuid(await fresh.ready()) == "again"


def test_loop_rebind_across_two_asyncio_run_sessions() -> None:
    async def first() -> DummyOwner:
        owner = DummyOwner()
        sub = await started(owner)
        owner.client.watch("sleep", "child").push(payload("one"))
        assert (await sub.ready()).exists is True
        await sub.aclose()
        return owner

    owner = asyncio.run(first())

    async def second() -> None:
        sub: Subscription[Any] = Subscription(owner, "sleep", "child", PARSE_SLEEP)
        await sub.__aenter__()
        owner.client.watch("sleep", "child").push(payload("two"))
        assert (await sub.ready()).exists is True

    asyncio.run(second())


def watch_client(api: HuckleberryAPI) -> FakeFirestoreClient:
    """Return the fake watch client behind the API's hub."""
    hub = api._watch_hub
    assert hub is not None
    assert hub._client is not None
    return hub._client


class _FakeRefreshResponse:
    status = 200
    reason = "OK"
    headers: dict[str, str] = {}
    request_info = SimpleNamespace(real_url="https://securetoken.googleapis.com/")
    history: tuple[Any, ...] = ()

    async def __aenter__(self) -> "_FakeRefreshResponse":
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def json(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        return {"id_token": "fresh-token", "refresh_token": "fresh-refresh", "expires_in": "3600"}

    async def text(self) -> str:
        return "{}"


async def test_refresh_session_token_rebuilds_client_and_resyncs() -> None:
    api = make_api()
    api.id_token = "stale-token"
    api.refresh_token = "stale-refresh"
    api.websession = SimpleNamespace(post=lambda *args, **kwargs: _FakeRefreshResponse())

    sub = api.watch_sleep("child")
    await sub.__aenter__()

    old_client = watch_client(api)
    old_watch = old_client.watch("sleep", "child")

    await api.refresh_session_token()

    new_client = watch_client(api)
    assert new_client is not old_client
    assert new_client.token == "fresh-token"
    await wait_until(lambda: old_client.closed)

    for _ in range(50):
        if old_watch.close_count:
            break
        await asyncio.sleep(0.02)
    assert old_watch.close_count == 1

    new_client.watch("sleep", "child").push(payload("after"))
    snapshot = await sub.ready()
    assert snapshot.resync is True
    assert timer_uuid(snapshot) == "after"


def test_legacy_registrations_survive_a_loop_change() -> None:
    api = make_api()

    sleep_updates: list[Any] = []
    feed_updates: list[Any] = []

    async def first() -> None:
        await api.setup_sleep_listener("child", sleep_updates.append)

    asyncio.run(first())

    async def second() -> None:
        await api.setup_feed_listener("child", feed_updates.append)

    asyncio.run(second())

    client = watch_client(api)
    client.watch("sleep", "child").push(payload("sleep"))
    client.watch("feed", "child").push({})

    assert sleep_updates and feed_updates
