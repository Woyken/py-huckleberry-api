# Plan: replace listener callbacks with async subscriptions

Status: draft for review
Scope: subscription transport and API (`setup_*_listener`, `stop_all_listeners`,
`refresh_session_token` listener handling)
Explicitly out of scope: the auth/transport/repository/service layering refactor, domain models,
bound resources such as `api.sleep(uid)`

## 0. Blocker found during review (must precede slice 1)

`src/huckleberry_api/api.py:188` is invalid Python 3:

```python
except aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError, ValueError:
```

`ast.parse` fails with `SyntaxError: multiple exception types must be parenthesized`, so the package
cannot be imported at all. The branch under review is the 0.4.10 commit, and **`v0.4.11`
(4410479 on `origin/main`) ships the same line**, which dates from `4e13b61` and is present in
**every release since 0.4.0** (`v0.4.0`–`v0.4.11`). Every "suite green" gate below is unverifiable
until this is fixed.

Fix: parenthesize the tuple, ship as **0.4.12**, rebase this plan onto `origin/main`. A test alone
cannot prevent recurrence: the locked `ruff` no longer has E999 and accepts this construct, `ty`
accepts it too, and `publish.yml` triggers on `release: published` with no dependency on CI. So add
an import check against the **built artifact** in `publish.yml` before the PyPI upload step:

```bash
uv build
uv run --isolated --no-project --with ./dist/*.whl python -c "import huckleberry_api"
```

`--isolated` alone is not enough: `uv run` still discovers the project and imports the source tree.

## 1. Does the change make sense?

Yes, for **document watches only**.

The current implementation cannot be fixed in place without changing its shape:

- `doc_ref.on_snapshot(...)` runs on a `firestore.Client` worker thread. The callback type is
  `Callable[[T], None]`, so consumers cannot `await`, cannot `asyncio.create_task`, and cannot
  touch loop-owned objects. Calling `create_task` from the callback raises
  `RuntimeError: no running event loop`.
- The registry key is `f"{collection}_{child_uid}"`, so a second subscribe for the same document
  overwrites the `unsubscribe` handle without calling it: the first consumer is silently dropped
  and its watch leaks.
- There is no per-subscription teardown, only `stop_all_listeners()`, which is global.
- `on_snapshot` has no error channel at all: recoverable failures retry forever, non-recoverable
  ones kill the consumer thread and go quiet.
- `refresh_session_token` reaches into listener internals and rebuilds them from stored callbacks.

An async subscription fixes all five, because lifetime becomes per-consumer and failures can be
raised.

No, for one-shot reads: `get_sleep` and friends stay coroutines. A stream is the wrong shape for
a single value.

## 2. Drawbacks and mitigations

| # | Drawback | Severity | Mitigation |
| --- | --- | --- | --- |
| 1 | Breaking change to a published 0.4.x API | high | `setup_*_listener` kept as a deprecated shim over the same transport; removal at 1.0.0 |
| 2 | `async for` never terminates on its own; naive consumers hang | high | document it; `async with` closes deterministically; tests use timeouts |
| 3 | Conflation drops intermediate snapshots | medium | explicit contract; the deprecated callback shim keeps per-snapshot delivery |
| 4 | The sync `firestore.Client` and its thread remain — the iterator is a facade | medium | accepted; no async watch exists in the Python SDK. Stated in docs |
| 5 | Iteration without `async with` leaks until GC | medium | `__aiter__` starts lazily, `aclose()` is explicit, docs show `async with` only |
| 6 | Coalescing changes timing vs. per-snapshot callbacks | medium | `Snapshot.seq` lets consumers detect gaps; documented as a semantic change |
| 7 | `latest` is unset until the first snapshot arrives | medium | `latest` is `Snapshot[T] \| None`; `await sub.ready()` returns it explicitly |
| 8 | A global `stop_all_listeners()` is incompatible with a shared hub | medium | in 0.5.x it closes every subscription and sink; pending `__anext__` ends with `StopAsyncIteration` |
| 9 | Closing a watch blocks the SDK thread for up to 1s (`BackgroundConsumer.stop()` joins) | medium | close off-loop via `asyncio.to_thread`; close all handles concurrently in `stop_all_listeners()` |
| 10 | A hub bound to one loop breaks the existing cross-loop usage (`asyncio.run()` per call) | medium | bind to the running loop; rebind only when the previously bound loop `is_closed()`, closing old handles off-loop; if it is still running, raise a clear error rather than killing its subscriptions |
| 11 | More test surface: async generators, timeouts, leaked subscriptions | medium | fake watch transport in the first PR; all new tests offline |
| 12 | `DocumentChange` deltas are not exposed | low | `Snapshot` stays extensible; deltas belong to query watches, added later |
| 13 | The deprecated callback still runs on the watch thread, so it still cannot touch the loop | low | unchanged 0.4.x contract, documented; deprecation steers users to subscriptions |
| 14 | Sharing one watch means a slow deprecated callback now delays new-style subscribers on the same document | medium | documented caveat for 0.5.x; the fix is to move to subscriptions. If it bites, dispatch passthrough sinks on a dedicated single-thread executor (preserves order, off the watch thread) |
| 15 | The SDK retries recoverable errors forever, so token expiry is invisible: `is_active` stays true and no `WatchFailed` is raised | medium | token expiry is handled by `gateway.rotate()` plus `as_of` staleness, not by the watchdog; documented in contract 4 |

Two corrections to earlier discussion, recorded so the plan is not built on them:

- **Deletion is not terminal.** Firestore keeps watching the reference and delivers a new snapshot
  if the document is recreated. `exists=False` is a state, not the end of iteration.
- **There is no stall detection.** Firestore sends no heartbeat, and "no snapshot" is
  indistinguishable from "no writes". The plan exposes `as_of` (from `read_time`, currently
  discarded) and an explicit `reconnect()`; it does not synthesise liveness.

## 3. Target API

Flat, matching the existing 0.4.x surface. Bound resources (`api.sleep(uid)`) are a later,
separate decision and are not assumed here.

```python
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

        if suspected_stall:
            await sub.reconnect()                  # document-wide; see contract 10
```

A stale `snap.as_of` means either "no writes" or "the token expired": the SDK retries
`Unauthenticated` forever without surfacing an error. `reconnect()` never touches credentials, so
the credential path is explicit:

```python
await api.refresh_session_token()   # rotates the transport with fresh credentials
await sub.reconnect()               # re-open the watch and resync
```

```python
@dataclass(frozen=True)
class Snapshot(Generic[T]):
    value: T | None
    exists: bool
    as_of: datetime
    seq: int            # monotonic per document handle, survives rotation
    resync: bool        # first snapshot after open, reconnect, or rotation
```

`Subscription` is generic; when domain models arrive it becomes `Subscription[SleepState]`
without changing its shape. The wire type is used here because domain models are out of scope.

Legacy shape, same transport, per-snapshot delivery and thread affinity preserved:

```python
await api.setup_sleep_listener(child_uid, callback)   # deprecated, unchanged semantics
```

## 4. Semantics contract

1. **Conflated, not queued (iterators only).** Each `__anext__` returns the newest snapshot since
   the last call. No backlog, no unbounded buffer, no backpressure fiction.
2. **`__aenter__` does not wait.** It registers the subscription and returns. Consumers that need
   the current value call `await sub.ready(timeout=None)`, which raises `WatchFailed` on timeout
   **and on terminal watch failure**, and leaves the subscription alive. If the handle already has
   a snapshot, the new subscription is seeded with it (`resync=True`, same `seq`), so a late
   joiner never waits for the next write.
3. **Deletion is a state.** The SDK does not hand the callback a `DocumentSnapshot` with
   `exists=False`; on deletion it delivers an **empty** `doc_snapshot` list and a `REMOVED`
   `DocumentChange`. The adapter maps "empty list, or a `REMOVED` change for this document" to
   `Snapshot(value=None, exists=False)`, and iteration continues.
4. **Errors are terminal, within what the SDK exposes.** A non-recoverable RPC termination raises
   `WatchFailed` from both `__anext__` and `ready()`; `__aexit__` closes without re-raising. A
   deliberate global stop ends iteration with `StopAsyncIteration`. The gateway runs a watchdog on
   `Watch.is_active` and maps "inactive without a requested close" to `WatchFailed`. The watchdog
   cannot see recoverable failures: `Unauthenticated`, `ServiceUnavailable` and
   `DeadlineExceeded` are retried indefinitely with `is_active` still true, so **token expiry is
   handled by `api.refresh_session_token()` (which calls `gateway.rotate()`) and by `as_of`
   staleness, not by `WatchFailed`.** `reconnect()` alone cannot recover an expired token.
5. **Rotation is invisible.** Token refresh restarts the transport; `seq` continues and
   `reconnects` increments. Sinks and subscriptions are not recreated. Both counters live on the
   document handle, so two subscribers on the same document see the same sequence numbers and a
   gap means conflation, not a missed write.
6. **One watch, many consumers.** `watch_*()` returns a new `Subscription`; the underlying handle
   is refcounted. Closing one subscription does not affect the others. A new subscription is
   seeded from the handle's last snapshot, so it starts with the current value instead of waiting
   for the next write.
7. **Cancellation is deterministic.** Leaving `async with` closes the subscription and awaits
   completion of the off-loop close.
8. **Callback shim preserves 0.4.x delivery.** It registers a passthrough sink that invokes the
   callback synchronously on the watch thread and skips `exists=False` snapshots — including the
   synthesized deletion snapshot — exactly as `_setup_listener` does today, so legacy callbacks
   never receive a `None` value. Payload parsing happens **inside** the guarded dispatch, not
   before it: a `ValidationError` is logged and that snapshot skipped. No exception may reach
   `Watch.push`, which would leave the SDK's change map dirty and kill the consumer thread for
   every subscriber on that document.
9. **Any registration path ensures a live hub.** After `stop_all_listeners()`, both `watch_*()`
   and `setup_*_listener()` start a fresh hub.
10. **`reconnect()` is document-wide.** It closes and reopens the handle, preserves `seq`,
    increments `reconnects`, and leaves every subscription on that handle alive. Because it is
    shared, the reopened watch re-delivers current state flagged `resync=True`, so other
    subscribers can tell a resync from a real transition. It never touches credentials.

## 5. Implementation slices

**Slice 0 — unbreak the package.** Parenthesize `api.py:188` on top of `origin/main`. Add an
import check against the built artifact in `publish.yml`. Release `0.4.12`.
Exit: `uv run python -c "import huckleberry_api"` succeeds; the publish workflow fails if the
built wheel cannot be imported.
Risk: none.

**Slice 1 — primitives (no public change).**
`subscriptions.py`: `Snapshot`, conflated slot, passthrough sink, `Subscription`, `WatchHub`,
`WatchGateway`. Fake watch transport in `tests/fakes/watch.py` that speaks the SDK's own callback
shape — a document list plus changes, so deletion arrives as an empty list and a `REMOVED` change —
and that can emit, delete, rotate, fail (non-recoverable, without emitting), stay active but silent
(recoverable retry), stall, deliver a malformed payload, and block on close.
Exit: offline tests for conflation, passthrough ordering, refcount, deletion, rotation,
non-recoverable death, active-but-silent, malformed payload isolation, late joiner seeding,
cancellation, `ready()` timeout and death, `reconnect()` + `resync`, and loop rebinding across two
sequential `asyncio.run()` sessions plus a concurrent two-loop registration.
Risk: medium (thread hop must use `loop.call_soon_threadsafe` with `RuntimeError` → `WatchFailed`).

**Slice 2 — one transport for both APIs.**
Migrate `_setup_listener` onto the gateway: the old callback becomes a passthrough sink, so
behaviour is unchanged but the per-document watch is shared and refcounted. Replace
`_listener_client` and `_listener_callbacks` with gateway state; replace `refresh_session_token`'s
recreate-from-callbacks loop with `gateway.rotate()`; update `stop_all_listeners()` to close
handles off-loop and concurrently. Add `api.watch_sleep(uid)` and `api.watch_pump(uid)`.
Exit: `tests/test_listeners.py` passes, with `test_listener_survives_token_refresh` rewritten
against the gateway; `tests/test_authentication.py::test_refresh_session_token_recreates_listener_client`
rewritten to assert rotation closes the old watch exactly once and the new client is live. Add an
offline test: `setup_*_listener` → `refresh_session_token` → `stop_all_listeners` → `setup_*_listener`
delivers again. Add an offline test that a blocking `close()` does not stall a concurrent ticker.
Risk: medium-high (largest slice; loop binding, rotation ordering).

**Slice 3 — remaining domains.**
`feed`, `health`, `diaper`, `activity`, `child`. Deprecation warnings on the callback API.
Exit: all seven `watch_*()` methods share one hub; no per-collection transport duplication remains.
Risk: low.

**Slice 4 — release.**
README rewrite with both APIs, towncrier fragment, `0.5.0`.
Exit: every README example runs.
Risk: low.

**Slice 5 — 1.0.0 cleanup.** Remove shims, `stop_all_listeners`, `setup_*_listener`.

## 6. Test plan

- Offline: fake transport drives all ten contract points; `asyncio.timeout` on every test.
- Offline: passthrough ordering — the fake transport emits three distinct snapshots while the
  callback blocks on a `threading.Event` (a sync callback cannot await `asyncio.Event`); assert
  three in-order deliveries with exact values.
- Offline: a legacy callback that raises, plus a deletion, leaves the shared watch alive and other
  subscribers receiving.
- Offline: a malformed payload is skipped, the watch stays active, and other sinks still receive.
- Offline: the real adapter maps an empty `doc_snapshot` plus a `REMOVED` change to
  `Snapshot(exists=False)`; the subscription yields it and the legacy callback is not invoked.
- Offline: a fake watch that dies without emitting makes `__anext__` raise `WatchFailed`.
- Offline: a fake watch that dies without emitting makes `await sub.ready()` raise `WatchFailed`
  instead of hanging.
- Offline: a fake watch that stays active but silent must **not** raise `WatchFailed`.
- Offline: a late joiner — sub1 receives a snapshot, sub2 is created, `await sub2.ready()` returns
  it immediately with `resync=True` and the same `seq`.
- Offline: simulated token expiry (watch silent, `is_active` true) — `refresh_session_token()`
  produces a `resync=True` snapshot on the existing subscription.
- Offline: `reconnect()` preserves `seq`, increments `reconnects`, keeps the subscription live, and
  the next snapshot has `resync=True`.
- Offline: closing one of two subscriptions leaves the other live.
- Offline: leak check — after `async with` exits, the hub has zero handles and the transport has
  received exactly one `close`. A `ready()` timeout leaves the handle count unchanged.
- Offline: `stop_all_listeners()` unblocks a pending `__anext__` with `StopAsyncIteration` and a
  subsequent `setup_*_listener` delivers again.
- Offline: a fake `close()` that blocks for 1s does not prevent a ticker task from running.
- Offline: two sequential `asyncio.run()` sessions on one API instance both receive snapshots, and
  the first session's handles were closed exactly once before rebinding.
- Offline: registering from a second loop while the first is still running raises a clear error and
  the first loop's subscription keeps running.
- Live (opt-in marker): sleep timer start/cancel via `watch_sleep()`, deletion, and token rotation
  during an active subscription.

## 7. Rollout

`0.4.12` fixes the syntax error. `0.5.0` adds subscriptions and deprecates the callback API.
`1.0.0` removes it. CHANGELOG calls out three behaviour changes: conflation for subscribers, the
removal of the global stop, and the fact that calling `setup_*_listener` twice for the same
document now registers two callbacks instead of silently replacing the first.

## 8. Decisions already made

- No buffer mode for `Subscription`: conflation is the contract, not a tunable. The callback shim
  is the documented escape hatch for per-snapshot consumers.
- No heartbeat: `as_of` + `reconnect()` instead.
- `watch_*()` returns a new `Subscription` per call; no shared mutable subscription object.
- `__aenter__` does not wait; `ready()` is explicit.
- No `api.close()`: the injected `aiohttp` session is caller-owned, so a closing method that
  touched it would be wrong. `async with` is the supported teardown.
