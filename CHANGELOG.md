# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- towncrier release notes start -->

## Unreleased

### Features

- Add child-specific medicine type listing and creation, plus medicine dose logging with all app-supported units. ([#33](https://github.com/Woyken/py-huckleberry-api/issues/33))


## [0.4.5] - 2026-09-15

### Features

- Add start, pause, resume, complete, and cancel support for pumping timers.


## [0.4.4] - 2026-09-15

### Features

- Add `get_sleep()` and `get_nursing()` for typed access to current sleep and nursing timer state.
- Add `log_temperature()` with Celsius, Fahrenheit, optional notes, and safe historical backfills.

### Bugfixes

- Allow `feed/{child_uid}/intervals` bottle rows to omit `amount`, so feeds saved without a volume validate instead of failing the whole `list_feed_intervals` query.
- Surface failed temperature, growth, and nursing history writes instead of continuing with inconsistent latest-entry summaries.
- Reject unsupported growth units and align growth and temperature history/summary payloads with live Firebase data.


## [0.4.3] - 2026-05-18

### Bugfixes

- Allow `childs/{child_id}.sweetspot.sweetSpotTimes` to contain sparse null slots so live Firebase child documents validate without raising type errors.


## [0.4.2] - 2026-04-27

### Features

- Add ability to subscribe to `child` collection containing details like Sweetspot ([#41](https://github.com/Woyken/py-huckleberry-api/issues/41))


## [0.4.1] - 2026-04-27

### Bugfixes

- Reset the sync listener Firestore client (`_listener_client`) inside `refresh_session_token()` so that listeners recreated after a token refresh use new credentials. Without this, recreated listeners reused the old credentials and silently stopped receiving updates after the first token refresh (~1 hour).


## [0.4.0] - 2026-03-24

### Features

- Added backfilled entry support for sleep, nursing, bottle, solids, diaper, potty, and growth logging, including explicit timestamp APIs for completed historical entries. ([#11](https://github.com/Woyken/py-huckleberry-api/issues/11))
- Added activity logging, interval listing, and realtime listener support.
  Changed interval range APIs to accept datetime objects instead of Unix timestamp integers. ([#12](https://github.com/Woyken/py-huckleberry-api/issues/12))


## [0.3.0] - 2026-03-15

### Features

- Added pump tracking support with strict Firebase pump models, range queries, and real-time pump listeners. Pump entries can now be logged with `log_pump(...)`, total amounts are stored using the live app's split-per-side payload shape. ([#18](https://github.com/Woyken/py-huckleberry-api/issues/18))

### Bugfixes

- Allowed breastfeed intervals to omit `leftDuration` and `rightDuration`, so `list_feed_intervals(...)` can parse Firebase entries that do not store per-side durations. ([#25](https://github.com/Woyken/py-huckleberry-api/issues/25))


## [0.2.3] - 2026-03-12

### Bugfixes

- Updated required python to 3.14
  Marked `lastUpdated` property as optional, older entries might not have this field ([#20](https://github.com/Woyken/py-huckleberry-api/issues/20))


## [0.2.2] - 2026-03-10

### Bugfixes

- Align growth Firebase models and listener handling with the live app's imperial payload shape, including composite units like `lbs.oz`, while keeping imperial growth writes on the supported field set.


## [0.2.1] - 2026-03-08

### Bugfixes

- Allow empty Firebase `prefs.last*` summary maps to validate after a child's sleep, feeding, or diaper history has been cleared. ([#deleted-last-summary-payloads](https://github.com/Woyken/py-huckleberry-api/issues/deleted-last-summary-payloads))


## [0.2.0] - 2026-03-07

### Features

- Added `HuckleberryAPI.log_potty()` for potty events stored in the shared diaper tracker; potty changes are observed through the existing diaper listener. ([#potty-api](https://github.com/Woyken/py-huckleberry-api/issues/potty-api))
- Migrate the client to strict Firebase schema models, require an injected `aiohttp` websession, and remove the separate solids interval API path.
