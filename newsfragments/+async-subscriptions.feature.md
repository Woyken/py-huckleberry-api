Add async subscriptions for Firestore documents.

`watch_sleep`, `watch_feed`, `watch_health`, `watch_diaper`, `watch_activity`,
`watch_pump` and `watch_child` return a `Subscription`: a conflated async
iterable and async context manager over one document. Snapshots carry `value`,
`exists`, `as_of`, `seq` and `resync`.

Behaviour changes for existing listener users:

- `setup_*_listener` is deprecated but keeps per-snapshot callbacks.
- `stop_all_listeners()` now also ends active subscriptions.
- Registering two callbacks for the same document keeps both delivering; the
  first is no longer left unstoppable.
