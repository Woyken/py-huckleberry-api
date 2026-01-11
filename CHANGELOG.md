# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **NEW API METHOD**: `log_bottle_feeding()` for logging bottle feedings as instant events
  - Supports bottle types: "Breast Milk", "Formula", "Mixed"
  - Records amount and units (ml or oz)
  - Creates interval documents in `feed/{child_uid}/intervals` with mode="bottle"
  - Updates `prefs.lastBottle` and document-level bottle preferences
- **TYPE SYSTEM**: Added bottle-specific type definitions
  - `BottleType` literal type for bottle contents
  - `VolumeUnits` literal type for measurement units
  - `LastBottleData` TypedDict for bottle preference structure
  - `FirebaseBottleInterval` TypedDict for raw Firebase structure
  - Refactored `FeedIntervalData` into union type (`BreastFeedIntervalData | BottleFeedIntervalData | SolidsFeedIntervalData`)
- **TESTS**: 6 new integration tests for bottle feeding functionality
- **DOCUMENTATION**: Updated DATA_STRUCTURE.md with bottle feeding interval examples

## [0.1.17] - 2025-12-16

### Fixed
- **METADATA**: Re-release with all commits included in tag
  - Previous release tag was created before lock file update

## [0.1.16] - 2025-12-16

### Fixed
- **METADATA**: Corrected PyPI project URLs to point to correct repository
  - Homepage, Repository, and Issues URLs now point to `py-huckleberry-api` instead of `huckleberry-homeassistant`
  - Fixes incorrect links on PyPI package page

## [0.1.15] - 2025-12-16

### Added
- **NEW API METHODS**: Added calendar/interval fetching methods for date range queries
  - `get_sleep_intervals(child_uid, start_timestamp, end_timestamp)` - Fetch sleep intervals
  - `get_feed_intervals(child_uid, start_timestamp, end_timestamp)` - Fetch feeding intervals
  - `get_diaper_intervals(child_uid, start_timestamp, end_timestamp)` - Fetch diaper intervals
  - `get_health_entries(child_uid, start_timestamp, end_timestamp)` - Fetch growth/health entries
  - `get_calendar_events(child_uid, start_timestamp, end_timestamp)` - Fetch all event types at once
- **OPTIMIZED QUERIES**: Dual query strategy for performance
  - Regular documents filtered by Firestore date range queries (server-side)
  - Multi-entry documents fetched separately and filtered in application code
  - Added `.order_by("start")` for indexed query optimization
- **TEST COVERAGE**: Added comprehensive test suite
  - 7 new calendar/interval fetching tests
  - 4 new tests for previously untested functionality (maintain_session, health/diaper listeners, explicit side resume)
  - Total: 34 tests, all passing

### Changed
- Handles both regular and multi-entry document formats efficiently
- Returns simple dictionaries with relevant fields for easy consumption

## [0.1.10] - 2025-12-04

### Fixed
- Fixed Pylance type errors in `api.py` by casting `TypedDict` objects to `dict[str, Any]` before passing to Firestore `set()` method.

## [0.1.9] - 2025-12-04

### Added
- **TYPE SAFETY**: Added strict `TypedDict` definitions for raw Firebase payloads
  - New types: `FirebaseSleepDocument`, `FirebaseFeedDocument`, `FirebaseDiaperInterval`, `FirebaseGrowthData`
  - New pref types: `SleepPrefs`, `FeedPrefs`, `DiaperPrefs`, `HealthPrefs`
  - New detail types: `LastSleepData`, `LastNursingData`, `LastSideData`, `LastDiaperData`

### Changed
- Updated `api.py` to use these types for `set()` and `update()` calls
- Corrected `HeightUnits` and `HeadUnits` to match app values ("in", "hin") instead of ("inches", "hinches")

## [0.1.8] - 2025-12-04

### Fixed
- **CRITICAL FIX**: Changed `start_sleep` and `start_feeding` to use `set(..., merge=True)` instead of `update`
  - Prevents `NotFound` errors when the child's tracking document does not exist (e.g., new child or cleared data)
  - Ensures robust initialization of tracking sessions

## [0.1.2] - 2025-12-04

### Changed
- **REFACTOR**: Consolidated listener setup methods into generic implementation
  - Removed ~100 lines of duplicated code across 4 listener methods
  - New private `_setup_listener()` method handles all collection types
  - Public methods (`setup_realtime_listener`, `setup_feed_listener`, etc.) now delegate to generic implementation
  - Maintains type-safe public API while eliminating code duplication
  - Token refresh recreation logic simplified

### Removed
- **BREAKING CHANGE**: Removed redundant `stop_sleep()` method
  - Use `complete_sleep()` instead - it's the better implementation
  - `complete_sleep()` preserves sleep details and uses proper interval ID format

### Fixed
- **CRITICAL FIX**: `complete_sleep()` now respects paused state
  - When sleep is paused, `timerEndTime` is set to mark the pause time
  - Completing a paused sleep now uses `timerEndTime` as end time (not current time)
  - Duration calculation now correctly excludes time after pause
  - Sleep end time in history now shows actual pause time, not when complete button was clicked

## [0.1.1] - 2025-12-02

### Added
- Growth tracking support
- Comprehensive type definitions

### Fixed
- Health tracker subcollection (uses `data` not `intervals`)
