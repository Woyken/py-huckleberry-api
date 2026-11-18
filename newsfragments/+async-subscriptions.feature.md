Add async subscriptions for Firestore documents and a child-scoped accessor
API.

`api.child(uid)` returns a stateless `ChildHandle` with one handle per tracker
(`sleep`, `feeding` incl. `nursing`/`bottle`/`solids`, `pump`, `activity`,
`diaper`, `health`). Tracker handles expose `watch()` where the tracker has a
live document, mutators for writes, and `history(start, end)` where the tracker
has interval history (`sleep`, `feeding`, `pump`, `activity`, `diaper`,
`health`).

`watch_sleep`, `watch_feed`, `watch_health`, `watch_diaper`, `watch_activity`,
`watch_pump` and `watch_child` remain as flat aliases. Both forms return the
same `Subscription`: a conflated async iterable and async context manager over
one document. Snapshots carry `value`, `exists`, `as_of`, `seq` and `resync`.

Behaviour changes for existing listener users:

- `setup_*_listener` is deprecated but keeps per-snapshot callbacks.
- A listener registered on a document that already has an active watch starts at
  the next change instead of replaying the current document, because both
  surfaces now share one watch.
- `stop_all_listeners()` now also ends active subscriptions.
- Registering two callbacks for the same document keeps both delivering; the
  first is no longer left unstoppable.
