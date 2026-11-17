# Plan: replace listener callbacks with async subscriptions

Status: draft for review
Base: `origin/main` at `b1ff8dc` (0.5.1, after the mixin split in #79; 0.5.1 only fixed
lastMedication parsing and does not touch listeners)
Scope: subscription transport and API (`setup_*_listener`, `stop_all_listeners`,
`refresh_session_token` listener handling)
Out of scope: the repository/service layering refactor, domain models, bound resources such as
`api.sleep(uid)`

## 0. Retraction: there is no syntax-error blocker

Earlier drafts of this plan claimed `except aiohttp.ContentTypeError, json.JSONDecodeError,
UnicodeDecodeError, ValueError:` was invalid Python and that every release since 0.4.0 was
unimportable. **That was wrong.** The construct is valid under PEP 758, which landed in Python
3.14, and this project requires `>=3.14`. All 17 modules parse cleanly under 3.14. The original
check ran a Python 3.12 interpreter.

Consequences: no 0.4.12 rescue release, no emergency fix, no rebase-because-broken. The line now
lives at `src/huckleberry_api/_client.py:31`.

Two optional hygiene items remain, neither blocking and neither a prerequisite for this plan:

- Parenthesize the tuple. It is valid but surprises readers and older tooling.
- Add an artifact import check to `publish.yml`
  (`uv build && uv run --isolated --no-project --with ./dist/*.whl python -c "import huckleberry_api"`).
  Useful against broken wheels, unrelated to this change.

## 1. Does the change make sense?

Yes, for **document watches only**. 0.5.0 split the code by feature but left the listener design
untouched: `_listeners.py` still holds the whole callback path, and `_client.py` still owns the
listener registry.

The implementation cannot be fixed in place without changing its shape:

- `doc_ref.on_snapshot(...)` runs on a `firestore.Client` worker thread. The callback type is
  `Callable[[T], None]`, so consumers cannot `await`, cannot `asyncio.create_task`, and cannot
  touch loop-owned objects. Calling `create_task` from the callback raises
  `RuntimeError: no running event loop`.
- The registry key is `f"{collection}_{child_uid}"`, so a second subscribe for the same document
  overwrites the `unsubscribe` handle and the recreation tuple without calling it. The first
  callback keeps delivering, but it can no longer be stopped or recreated, so its watch leaks.
- There is no per-subscription teardown, only `stop_all_listeners()`, which is global.
- `on_snapshot` has no error channel at all: recoverable failures retry forever, non-recoverable
  ones kill the consumer thread and go quiet.
- `refresh_session_token()` must tear down every listener and rebuild it from a stored
  `(collection, child_uid, callback)` tuple via the `_recreate_listeners()` hook.

An async subscription fixes all five, because lifetime becomes per-consumer and failures can be
raised.

No, for one-shot reads: `get_sleep` and friends stay coroutines. A stream is the wrong shape for
a single value.

## 2. Drawbacks and mitigations

| # | Drawback | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Breaking change to a published 0.5.x API | high | `setup_*_listener` kept as a deprecated shim over the same transport; removal at 1.0.0 |
| 2 | `async for` never terminates on its own; naive consumers hang | high | document it; `async with` closes deterministically; tests use timeouts |
| 3 | Conflation drops intermediate snapshots | medium | explicit contract; the deprecated callback shim keeps per-snapshot delivery |
| 4 | The sync `firestore.Client` and its thread remain — the iterator is a facade | medium | accepted; no async watch exists in the Python SDK. Stated in docs |
| 5 | Iteration without `async with` is never released — the hub holds a strong reference, so the subscription outlives the local variable | medium | `aclose()` is explicit; the true lifetime is until `stop_all_listeners()`/rebind, and docs show `async with` only |
| 6 | Coalescing changes timing vs. per-snapshot callbacks | medium | `Snapshot.seq` lets consumers detect gaps; documented as a semantic change |
| 7 | `latest` is unset until the first snapshot arrives | medium | `latest` is `Snapshot[T] \| None`; `await sub.ready()` returns it explicitly |
| 8 | A global `stop_all_listeners()` is incompatible with a shared hub | medium | it keeps working but now also ends active subscriptions; pending `__anext__` ends with `StopAsyncIteration` |
| 9 | Closing a watch blocks the SDK thread for up to 1s (`BackgroundConsumer.stop()` joins) | medium | close off-loop via `asyncio.to_thread`; close all handles concurrently in `stop_all_listeners()` |
| 10 | A hub bound to one loop breaks the existing cross-loop usage (`asyncio.run()` per call) | medium | bind to the running loop for subscription delivery only; a rebind repoints delivery, drops subscriptions whose loop is closed, then closes any handle left at zero refs — a handle that still has a sink survives; registering a subscription from a second loop while the previous one is still running raises a clear error, while sink-only registration is unaffected |
| 11 | More test surface: async generators, timeouts, leaked subscriptions | medium | fake Firestore client/ref/watch in the first PR; all new tests offline |
| 12 | `DocumentChange` deltas are not exposed | low | `Snapshot` stays extensible; deltas belong to query watches, added later |
| 13 | The deprecated callback still runs on the watch thread, so it still cannot touch the loop | low | unchanged 0.5.x contract, documented; deprecation steers users to subscriptions |
| 14 | Sharing one watch means a slow deprecated callback now delays new-style subscribers on the same document | medium | documented caveat; the fix is to move to subscriptions. If it bites, dispatch passthrough sinks on a dedicated single-thread executor (preserves order, off the watch thread) |
| 15 | The SDK retries recoverable errors forever, so token expiry is invisible: `is_active` stays true and no `WatchFailed` is raised | medium | token expiry is handled by `api.refresh_session_token()` → `gateway.rotate()` plus `as_of` staleness, not by the watchdog; see contract 4 |

Two corrections to earlier discussion, recorded so the plan is not built on them:

- **Deletion is not terminal.** Firestore keeps watching the reference and delivers a new snapshot
  if the document is recreated. `exists=False` is a state, not the end of iteration.
- **There is no stall detection.** Firestore sends no heartbeat, and "no snapshot" is
  indistinguishable from "no writes". The plan exposes `as_of` (from `read_time`, currently
  discarded) and an explicit `reconnect()`; it does not synthesise liveness.

## 3. Target API

Flat, matching the current surface. Bound resources (`api.sleep(uid)`) are a later, separate
decision and are not assumed here.

`watch_*()` is a **sync factory**: it performs no I/O and returns the `Subscription`.
Authentication, hub lookup and watch creation happen on first use — `__aenter__`, the first
`__anext__`, `ready()`, or `reconnect()` — which is why the example needs no `await` between
`async with` and the call.

```python
from huckleberry_api.subscriptions import Subscription  # public module, see §8
from huckleberry_api.firebase_types import FirebaseSleepDocumentData

async with api.watch_sleep(child_uid) as sub:      # Subscription[FirebaseSleepDocumentData]
    sub.latest        # Snapshot[FirebaseSleepDocumentData] | None
    sub.reconnects    # how many transport restarts occurred

    async for snap in sub:                         # transitions, conflated
        if not snap.exists:                        # document deleted; keep iterating
            continue
        if snap.resync:                            # after open/reconnect/rotation
            rebuild_view(snap.value)
        else:
            apply_transition(snap.value)
```

A stale `snap.as_of` means either "no writes" or "the token expired": the SDK retries
`Unauthenticated` forever without surfacing an error. Refresh rotates the transport *and* reopens
the watch, so it is the whole recovery path when credentials are the suspect:

```python
await api.refresh_session_token()   # rotate: rebuild client, reopen watches, resync
```

`reconnect()` is the separate, credential-free path, for when you suspect a dead watch but not an
expired token:

```python
await sub.reconnect()               # document-wide; see contract 10
```

```python
@dataclass(frozen=True)
class Snapshot(Generic[T]):
    value: T | None
    exists: bool
    as_of: datetime    # aware UTC; read_time arrives as proto-plus DatetimeWithNanoseconds
    seq: int           # handle receipt counter; gaps mean snapshots were conflated away
    resync: bool       # sticky per subscription; set on open/rotate/reconnect, cleared on delivery
```

`Subscription` is generic; when domain models arrive it becomes `Subscription[SleepState]`
without changing its shape. The wire type is used here because domain models are out of scope.

Legacy shape, same transport, per-snapshot delivery and thread affinity preserved. One limitation: a
callback attached to a document that already has an active watch starts at the next change and does
not receive the stored snapshot:

```python
await api.setup_sleep_listener(child_uid, callback)   # deprecated; no replay on join
```

## 4. Semantics contract

1. **Conflated, not queued (iterators only).** Each `__anext__` returns the newest snapshot since
   the last call. No backlog, no unbounded buffer, no backpressure fiction.
2. **`__aenter__` does not wait for the first snapshot.** It performs authentication and
   registration — so it can do credential I/O — but it returns without waiting for a snapshot.
   Consumers that need the current value call `await sub.ready(timeout=None)`, which
   registers first if the subscription has not been started, then returns the current or next
   snapshot. Its three outcomes are `TimeoutError` (timeout), `WatchFailed` (terminal failure) and
   `StopAsyncIteration` (global stop, or a subscription already ended by `aclose()`; see contract
   4), and it never re-registers a closed subscription. The subscription stays alive after a
   timeout, so a timeout never costs a healthy watch. If the handle already has a snapshot, the
   subscription is seeded with it (`resync=True`, reusing that receipt's `seq`), so a late joiner
   never waits for the next write.
3. **Deletion is a state.** The SDK does not hand the callback a `DocumentSnapshot` with
   `exists=False`; on deletion it delivers an **empty** `doc_snapshot` list and a `REMOVED`
   `DocumentChange`. The adapter maps "empty list, or a `REMOVED` change for this document" to
   `Snapshot(value=None, exists=False)`, and iteration continues. It must key on
   `DocumentChange.type == REMOVED`, because the snapshot attached to that change still reports
   `exists=True`.
4. **Errors are terminal, within what the SDK exposes.** A non-recoverable RPC termination raises
   `WatchFailed` from both `__anext__` and `ready()`; `__aexit__` closes without re-raising. A
   deliberate global stop ends iteration with `StopAsyncIteration`, and a `ready()` blocked at that
   moment ends the same way rather than hanging. The gateway runs a watchdog on
   `Watch.is_active` and maps "inactive without a requested close" to `WatchFailed`. The watchdog
   cannot see recoverable failures: `Unauthenticated`, `ServiceUnavailable` and
   `DeadlineExceeded` are retried indefinitely with `is_active` still true, so **token expiry is
   handled by `api.refresh_session_token()` (which calls `gateway.rotate()`) and by `as_of`
   staleness, not by `WatchFailed`.** `reconnect()` alone cannot recover an expired token.
5. **Rotation is invisible.** Token refresh restarts the transport; `reconnects` increments and
   every affected subscription gets one `resync=True` delivery. Sinks and subscriptions are not
   recreated. `reconnect` counts live on the document handle, so two subscribers on the same
   document see the same count. `gateway.rotate()` builds a **new** `firestore.Client` with the
   fresh `id_token` and closes the one it replaces, so hourly refreshes do not leak a channel. The
   reason a new client is needed is that nothing ever updates the existing credential: `refresh()`
   is a no-op, so `.token` keeps the expired value and every RPC on the old client keeps presenting
   it. `refresh_session_token()` therefore still clears `_firestore_client` and
   `_firestore_client_loop`, exactly as it does today.
6. **One watch, many consumers.** `watch_*()` returns a new `Subscription`; the underlying handle is
   refcounted over **subscriptions plus registered sinks**. Closing one subscription does not
   affect the others, and a handle whose only consumer is a deprecated `setup_*_listener` sink
   stays open. A handle whose refcount reaches zero releases its watch. The sync client itself is
   **gateway-scoped and shared** by every handle, so it is closed only when the last handle goes,
   on rotation, and on `stop_all_listeners()` — never because one document's last consumer left. A
   new subscription is seeded from the handle's last snapshot, so it starts with the current value
   instead of waiting for the next write. If the handle has already failed, first use — `__aenter__`,
   `__anext__`, `ready()` or `reconnect()` — starts a fresh watch rather than joining a dead one.
7. **Cancellation is deterministic.** Leaving `async with` closes the subscription and awaits
   completion of the off-loop close, shielded so a second cancellation cannot abandon a half-closed
   handle. A cancelled exit still results in exactly one transport `close`.
8. **Payload validation happens once per handle, inside the guarded dispatch.** The document's
   collection fixes its wire type, so the adapter validates once, before fan-out. A
   `ValidationError` is logged and that snapshot is skipped for every consumer on the handle: the
   watch stays alive, a callback is not invoked, a subscriber is not woken, and a later good
   payload is delivered normally. No exception may reach `Watch.push`, which would leave the SDK's
   change map dirty and kill the consumer thread for every subscriber on that document. The legacy
   shim additionally skips `exists=False` snapshots — including the synthesized deletion snapshot —
   exactly as `_setup_listener` does today, so legacy callbacks never receive a `None` value.
9. **Any registration path ensures a live hub.** After `stop_all_listeners()`, the next
   registration — `setup_*_listener()`, or the first use of a `watch_*()` subscription — starts a
   fresh hub.
10. **`reconnect()` is document-wide.** It closes and reopens the handle, increments `reconnects`,
    and leaves every subscription on that handle alive. `seq` is a per-receipt counter on the
    handle: it increments for every snapshot the transport delivers, including resyncs and
    snapshots that conflation later discards. The seed of a late joiner is not a new receipt — it
    replays the last one and reuses its `seq` — so a joiner's first value carries no artificial
    gap, and a gap still means "snapshots were coalesced away". Because the handle is shared, the post-reconnect redelivery is flagged
    `resync=True` on every subscription, so other consumers can tell a resync from a real
    transition. `resync` is sticky per subscription: it is set on open, rotate and reconnect, and
    cleared only by the next *delivered* snapshot, so conflation cannot swallow it. It never
    touches credentials.

## 5. Implementation slices

**Slice 1 — primitives (no public change to `HuckleberryAPI`).**
New `src/huckleberry_api/subscriptions.py`: `Snapshot`, `Subscription` and `WatchFailed` are
public (`__all__`), while the conflated slot, `_WatchHub` and `_WatchGateway` stay private in the
same module. Add `huckleberry_api.subscriptions` to `PUBLIC_MODULES` in
`tests/test_api_surface.py`; `EXPECTED_TOP_LEVEL` is unchanged because the package still exports
only `HuckleberryAPI`. Not wired into `HuckleberryAPI.__bases__` yet.
Tests fake the Firestore boundary — `Client`, `DocumentReference`, `Watch` — in
`tests/fakes/watch.py`, so the real adapter is under test and the fake passes an aware
`DatetimeWithNanoseconds` as `read_time`, the way proto-plus does for `TargetChange.read_time`.
It can emit, delete (empty document list plus a `REMOVED` change),
rotate, fail non-recoverably without emitting, go silent while staying active (one mode; this is
both the recoverable-retry and the stall case, since there is no signal to distinguish them),
deliver a malformed payload, and block on close.
Exit: offline tests for conflation, passthrough ordering, refcount over subscriptions **and**
sinks (a sink-only handle survives another subscription's exit), deletion, rotation,
non-recoverable death, active-but-silent, malformed payload isolation and recovery, late joiner
seeding, sticky `resync` under a slow consumer, `seq` gap behaviour across conflation, reconnect
and rotation, aware-UTC `as_of` from a `DatetimeWithNanoseconds`, cancellation including
cancel-during-close, `ready()` timeout vs death vs stop, `reconnect()`, gateway client lifetime on
rotate/stop/last-handle, and loop rebinding across two sequential `asyncio.run()` sessions plus a
concurrent two-loop registration.
Risk: medium (thread hop must use `loop.call_soon_threadsafe` with `RuntimeError` → `WatchFailed`).

**Slice 2 — one transport for both APIs.**
Move the callback path from `_listeners.py` onto the gateway: the old callback becomes a
passthrough sink, so behaviour is unchanged but the per-document watch is shared and refcounted.
Replace `_listener_client`, `_listeners` and `_listener_callbacks` in `_client.py` with gateway
state; replace the `_recreate_listeners()` hook (no-op in `HuckleberryBase`, override in
`ListenerMixin`) with `gateway.rotate()`; rewrite `refresh_session_token()` to clear
`_firestore_client`/`_firestore_client_loop` and rebuild the sync client from the fresh token;
update `stop_all_listeners()` to close handles off-loop and concurrently. Add
`api.watch_sleep(uid)` and `api.watch_pump(uid)` to a `SubscriptionMixin` (or to `ListenerMixin`).
Exit:
- `tests/test_listeners.py` passes, with `test_listener_survives_token_refresh` rewritten against
  the gateway and still asserting the client object changed.
- `tests/test_authentication.py::test_refresh_session_token_recreates_listener_client` rewritten to
  assert rotation closes the old watch exactly once, rebuilds the client from the new token, and
  leaves subscriptions intact.
- `tests/test_api_surface.py` updated: add the `watch_*` names to `EXPECTED_PUBLIC_METHODS`, and
  replace `test_listener_recreation_hook_comes_from_listener_mixin` (the hook it pins is what this
  slice deletes) with a gateway-rotation equivalent. Keep
  `test_no_mixin_shadows_a_base_or_another_mixin_method` passing by registering the new mixin in
  `HuckleberryAPI.__bases__` exactly once.
- New offline tests: `setup_*_listener` → `refresh_session_token` → `stop_all_listeners` →
  `setup_*_listener` delivers again; a blocking `close()` does not stall a concurrent ticker; a
  sink-only handle is not closed by an unrelated subscription's exit or by a loop rebind.
Risk: medium-high (largest slice; loop binding, rotation ordering).

**Slice 3 — remaining domains.**
`feed`, `health`, `diaper`, `activity`, `child`. Deprecation warnings on the callback API.
Exit: all seven `watch_*()` methods share one hub; no per-collection transport duplication remains;
`EXPECTED_PUBLIC_METHODS` covers the full new surface.
Risk: low.

**Slice 4 — release.**
README rewrite with both APIs, towncrier fragment, `0.6.0`.
Exit: every README example runs.
Risk: low.

**Slice 5 — 1.0.0 cleanup.** Remove shims, `stop_all_listeners`, `setup_*_listener`.

## 6. Test plan

- Offline: fake client/ref/watch drives all ten contract points; `asyncio.timeout` on every test.
- Offline: passthrough ordering — the fake emits three distinct snapshots while the callback blocks
  on a `threading.Event` (a sync callback cannot await `asyncio.Event`); assert three in-order
  deliveries with exact values.
- Offline: a legacy callback that raises, plus a deletion, leaves the shared watch alive and other
  subscribers receiving.
- Offline: a malformed payload reaches no consumer — no callback, no wake-up — the watch stays
  active, and a later good payload reaches both a sink and a subscription.
- Offline: the real adapter maps an empty `doc_snapshot` plus a `REMOVED` change to
  `Snapshot(exists=False)`; the subscription yields it and the legacy callback is not invoked.
- Offline: a fake watch that dies without emitting makes `__anext__` raise `WatchFailed`.
- Offline: a fake watch that dies without emitting makes `await sub.ready()` raise `WatchFailed`
  instead of hanging.
- Offline: a fake watch that stays active but silent must **not** raise `WatchFailed`.
- Offline: `ready(timeout=...)` on a silent watch raises `TimeoutError`, not `WatchFailed`, and the
  subscription stays usable.
- Offline: a late joiner — sub1 receives a snapshot, sub2 is created, `await sub2.ready()` returns
  it immediately with `resync=True` and the replayed receipt's `seq`.
- Offline: a late joiner race — sub2 is created, a new write lands before sub2's first
  `__anext__`, and sub2's first delivered snapshot still has `resync=True`.
- Offline: sticky resync under conflation — slow consumer, rotate, then a write before its next
  `__anext__`; the first delivered snapshot is flagged `resync=True`.
- Offline: `seq` is a receipt counter — a subscriber that skips a conflated snapshot sees a gap, a
  seed reuses the last receipt's `seq`, and `seq` never repeats or rewinds across reconnect and
  rotation.
- Offline: `as_of` is timezone-aware UTC (`utcoffset() == timedelta(0)`) and passes through the
  `DatetimeWithNanoseconds` the SDK supplies, with no `.ToDatetime()` call.
- Offline: a subscription created on a failed handle starts a fresh watch rather than joining it.
- Offline: simulated token expiry (watch silent, `is_active` true) — `refresh_session_token()`
  produces a `resync=True` snapshot on the existing subscription.
- Offline: `reconnect()` increments `reconnects`, keeps the subscription live, and the next
  snapshot has `resync=True` and a higher `seq`.
- Offline: `ready()` blocked when `stop_all_listeners()` runs ends with `StopAsyncIteration`
  instead of hanging.
- Offline: two subscriptions on one document — the fake counts exactly one `on_snapshot`, and after
  both `rotate()` and `reconnect()` each subscriber gets `resync=True` with the same incremented
  `reconnects`.
- Offline: the gateway-scoped client is closed on rotation, on `stop_all_listeners()`, and when the
  last handle closes — but not when one document's last consumer leaves while others remain.
- Offline: closing one of two subscriptions leaves the other live.
- Offline: a sink-only handle survives another subscription's exit — register via
  `setup_*_listener`, then run and exit an unrelated `async with watch_*()` for the same document.
- Offline: leak check — after `async with` exits, the hub has zero handles and the transport has
  received exactly one `close`. A `ready()` timeout leaves the handle count unchanged.
- Offline: `stop_all_listeners()` unblocks a pending `__anext__` with `StopAsyncIteration` and a
  subsequent `setup_*_listener` delivers again.
- Offline: a fake `close()` that blocks for 1s does not prevent a ticker task from running, and
  cancelling the exit still yields exactly one `close`.
- Offline: two sequential `asyncio.run()` sessions on one API instance both receive snapshots;
  the first session's sink-free handle is closed exactly once when its last subscription is
  dropped on rebind.
- Offline: rebind with a surviving sink — session one registers a sink and abandons a subscription
  on the same document, session two rebinds; the sink handle stays open and keeps delivering while
  the abandoned subscription's handle path is closed once.
- Offline: a sink-only handle registered from the second loop still delivers while the first loop
  is running (no loop affinity for sinks, no error).
- Offline: two sequential `asyncio.run()` **legacy** registrations — `asyncio.run(setup_sleep_listener())`
  then `asyncio.run(setup_feed_listener())` — both callbacks still deliver.
- Offline: registering from a second loop while the first is still running raises a clear error and
  the first loop's subscription keeps running.
- Live (`integration` marker, the repo's existing credential marker; no new marker, since
  `pytest.ini` uses `--strict-markers`): sleep timer start/cancel via `watch_sleep()`, and token
  rotation during an active subscription. Deletion stays offline-only: it would mutate the shared
  account, and the adapter mapping is fully covered by the fake, which models the real SDK shape.

## 7. Rollout

`0.6.0` adds subscriptions and deprecates the callback API; `stop_all_listeners()` keeps working
but now also ends active subscriptions, and a later registration starts a fresh hub. `1.0.0`
removes the shims and the global stop. The `0.6.0` CHANGELOG calls out three behaviour changes:
conflation for subscribers, the new stop semantics, and the fact that calling `setup_*_listener`
twice for the same document now registers two callbacks instead of leaving the first one delivering
but unstoppable.

## 8. Decisions already made

- No buffer mode for `Subscription`: conflation is the contract, not a tunable. The callback shim
  is the documented escape hatch for per-snapshot consumers.
- No heartbeat: `as_of` + `reconnect()` instead.
- `watch_*()` returns a new `Subscription` per call; no shared mutable subscription object.
- `__aenter__` does not wait for a snapshot — it still authenticates and registers; `ready()` is
  the explicit wait, and it also works as the first call (lazy registration).
- No `api.close()`: the injected `aiohttp` session is caller-owned, so a closing method that
  touched it would be wrong. `async with` is the supported teardown.
- `Snapshot`, `Subscription` and `WatchFailed` are public in `huckleberry_api.subscriptions`; the
  slot, hub and gateway stay private in the same module. Consumers annotate with these types, so
  they need a supported import path.
- Tests fake the Firestore boundary (`Client`, `DocumentReference`, `Watch`), not our hub, so the
  adapter is exercised by the same fake that drives the semantics.
- A handle's refcount covers subscriptions **and** sinks: the deprecated callback path must keep a
  document alive on its own, exactly as it does today.
- `watch_*()` is a sync factory: no I/O until `__aenter__`/`__anext__`, so the documented
  `async with api.watch_sleep(uid) as sub` works without an `await`.
- The sync `firestore.Client` is gateway-scoped and shared; handles own watches, not clients.
- `as_of` passes `read_time` through unchanged; it is an aware `DatetimeWithNanoseconds`, not a
  protobuf `Timestamp`, so no conversion is applied.
- Build on the mixin layout rather than re-splitting modules: subscription code belongs in
  `subscriptions.py` / `_listeners.py`, and `_client.py` keeps the client plumbing.
