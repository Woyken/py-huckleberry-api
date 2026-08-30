# Huckleberry API

[![Integration Tests](https://github.com/Woyken/py-huckleberry-api/actions/workflows/integration-tests.yml/badge.svg)](https://github.com/Woyken/py-huckleberry-api/actions/workflows/integration-tests.yml)

Python API client for the Huckleberry baby tracking app using Firebase Firestore.

## Overview

This is a reverse-engineered API client that connects directly to Huckleberry's Firebase backend using the official Google Cloud Firestore SDK. It provides programmatic access to baby tracking features including sleep, feeding, diaper changes, and growth measurements.

## Features

- 🔐 **Firebase Authentication**: Secure email/password authentication with automatic token refresh
- 💤 **Sleep Tracking**: Start, pause, resume, cancel, and complete sleep sessions
- 🍼 **Feeding Tracking**: Track breastfeeding with left/right side switching
- 🤱 **Pumping Tracking**: Log pumping sessions and fetch latest/history
- 🧷 **Diaper Changes**: Log pee, poo, both, or dry checks with color/consistency
- 📏 **Growth Measurements**: Record weight, height, and head circumference
- ✏️ **Safe History Changes**: Edit or delete exact records with revision checks
- 🔄 **Real-time Updates**: Firebase snapshot listeners for instant synchronization
- 👶 **Child Management**: Support for multiple children profiles

## Installation

```bash
uv add huckleberry-api
# or
pip install huckleberry-api
```

## Quick Start

```python
import asyncio
from datetime import datetime, timedelta
import aiohttp

from huckleberry_api import HuckleberryAPI

async def main() -> None:
    async with aiohttp.ClientSession() as websession:
        api = HuckleberryAPI(
            email="your-email@example.com",
            password="your-password",
            timezone="Europe/London",
            websession=websession,
        )

        await api.authenticate()

        user_doc = await api.get_user()
        child_uid = user_doc.childList[0].cid

        await api.start_sleep(child_uid)
        await api.complete_sleep(child_uid)
        await api.log_sleep(
            child_uid,
            start_time=datetime.now() - timedelta(hours=3),
            end_time=datetime.now() - timedelta(hours=2),
        )

        await api.start_nursing(child_uid, side="left")
        await api.switch_nursing_side(child_uid)
        await api.complete_nursing(child_uid)
        await api.log_nursing(
            child_uid,
            start_time=datetime.now() - timedelta(hours=4),
            end_time=datetime.now() - timedelta(hours=3, minutes=30),
            side="right",
        )

        await api.log_bottle(
            child_uid,
            start_time=datetime.now() - timedelta(hours=1),
            amount=120.0,
            bottle_type="Formula",
            units="ml",
        )
        await api.log_pump(
            child_uid,
            start_time=datetime.now(),
            total_amount=120.0,
            duration=900,
            units="ml",
        )
        await api.log_diaper(
            child_uid,
            start_time=datetime.now() - timedelta(minutes=45),
            mode="both",
            pee_amount="medium",
            poo_amount="medium",
            color="yellow",
            consistency="solid",
        )
        await api.log_growth(
            child_uid,
            start_time=datetime.now() - timedelta(days=1),
            weight=5.2,
            height=52.0,
            head=35.0,
            units="metric",
        )


asyncio.run(main())
```

## Real-time Listeners

Set up real-time listeners for instant updates:

```python
import aiohttp

def on_sleep_update(data):
    timer = data.get("timer", {})
    print(f"Sleep active: {timer.get('active')}")
    print(f"Sleep paused: {timer.get('paused')}")

async def main() -> None:
    async with aiohttp.ClientSession() as websession:
        api = HuckleberryAPI(
            email="your-email@example.com",
            password="your-password",
            timezone="Europe/London",
            websession=websession,
        )
        await api.authenticate()
        user_doc = await api.get_user()
        child_uid = user_doc.childList[0].cid

        await api.setup_sleep_listener(child_uid, on_sleep_update)
        await api.stop_all_listeners()
```

## API Methods

### Authentication
- `await authenticate()` - Authenticate with Firebase
- `await ensure_session()` - Ensure valid auth/session state
- `await refresh_session_token()` - Refresh expired token

### Children
- `await get_user()` - Get full `users/{uid}` document
- `await get_child(child_uid)` - Get a single child profile by id

### Sleep Tracking
- `await start_sleep(child_uid)` - Start sleep session
- `await pause_sleep(child_uid)` - Pause active session
- `await resume_sleep(child_uid)` - Resume paused session
- `await cancel_sleep(child_uid)` - Cancel without saving
- `await complete_sleep(child_uid)` - Complete and save to history
- `await log_sleep(child_uid, start_time=..., end_time=..., details=None)` - Log a completed sleep interval with explicit timestamps

### Feeding Tracking
- `await start_nursing(child_uid, side)` - Start breastfeeding session
- `await pause_nursing(child_uid)` - Pause active session
- `await resume_nursing(child_uid, side)` - Resume paused session
- `await switch_nursing_side(child_uid)` - Switch left/right
- `await cancel_nursing(child_uid)` - Cancel without saving
- `await complete_nursing(child_uid)` - Complete and save to history
- `await log_nursing(child_uid, start_time=..., end_time=..., side="left")` - Log a completed breastfeeding interval with explicit timestamps
- `await log_bottle(child_uid, start_time=..., amount=..., bottle_type=..., units=...)` - Log bottle feeding with an explicit event timestamp
  - `bottle_type`: "Breast Milk", "Formula", "Cow Milk", "Soy Milk", etc.
  - `amount`: Volume fed (e.g., 120.0)
  - `units`: "ml" or "oz"

### Pumping Tracking
- `await log_pump(child_uid, start_time=..., total_amount=..., duration=..., units=...)` - Log pumping entry; total entries are stored split evenly across `leftAmount` and `rightAmount`
- `await list_pump_intervals(child_uid, start_time, end_time)` - List pump history for a range using `datetime` objects

### Solids Tracking
- `await list_solids_curated_foods()` - List curated solids food catalog
- `await list_solids_custom_foods(child_uid, include_archived=False)` - List custom solids foods
- `await create_solids_custom_food(child_uid, name, image="")` - Create custom solids food
- `await log_solids(child_uid, start_time=..., foods=..., notes="", reaction=None, food_note_image=None)` - Log solids meal with an explicit event timestamp

### Diaper Tracking
- `await log_diaper(child_uid, start_time=..., mode=..., pee_amount=..., poo_amount=..., color=..., consistency=...)` - Log diaper change with an explicit event timestamp
- `await log_potty(child_uid, start_time=..., mode=..., how_it_happened=..., pee_amount=..., poo_amount=..., color=..., consistency=...)` - Log potty event with an explicit event timestamp
  - `mode`: "pee", "poo", "both", or "dry"
  - `color`: "yellow", "green", "brown", "black", "red"
  - `consistency`: "runny", "soft", "solid", "hard"

### Growth Tracking
- `await log_growth(child_uid, start_time=..., weight=..., height=..., head=..., units=...)` - Log measurements with an explicit event timestamp
  - `units`: "metric" (kg/cm) or "imperial" (lbs/inches)
- `await get_latest_growth(child_uid)` - Get latest measurements

### Revision-safe History Mutations

History record list methods retain the Firestore document identity and update
revision needed for safe edits and deletes:

- `await list_sleep_interval_records(child_uid, start_time, end_time)`
- `await list_feed_interval_records(child_uid, start_time, end_time)`
- `await list_diaper_interval_records(child_uid, start_time, end_time)`
- `await list_health_entry_records(child_uid, start_time, end_time)`

Each returned record contains `reference` and `data`. Pass the unmodified
`reference` to a mutation method:

- `await update_sleep_interval(child_uid, reference, start_time=..., end_time=...)`
- `await delete_sleep_interval(child_uid, reference)`
- `await update_nursing_interval(child_uid, reference, start_time=..., left_duration=..., right_duration=..., last_side=...)`
- `await update_bottle_interval(child_uid, reference, start_time=..., amount=..., bottle_type=..., units=...)`
- `await delete_feed_interval(child_uid, reference)`
- `await update_diaper_interval(child_uid, reference, start_time=..., mode=...)`
- `await delete_diaper_interval(child_uid, reference)`
- `await update_growth_entry(child_uid, reference, start_time=..., weight=..., height=..., head=..., units=...)`
- `await delete_growth_entry(child_uid, reference)`

Mutations run in a Firestore transaction. They reject stale revisions, support
regular and batched history documents, and repair the affected `prefs.last*`
cache atomically. List the record again after a successful edit before making
another change because its revision will have changed.

### Real-time Listeners
- `await setup_sleep_listener(child_uid, callback)` - Listen to sleep updates
- `await setup_feed_listener(child_uid, callback)` - Listen to feeding updates
- `await setup_health_listener(child_uid, callback)` - Listen to health updates
- `await setup_pump_listener(child_uid, callback)` - Listen to pump updates
- `await stop_all_listeners()` - Stop all active listeners

## Type Definitions

The package includes Pydantic models for type safety.

Canonical Firebase schema definitions are in `src/huckleberry_api/firebase_types.py`.

- `FirebaseChildDocument` - Child profile information
- `FirebaseSleepDocumentData` / `FirebaseSleepTimerData` - Sleep tracking data
- `FirebaseFeedDocumentData` / `FirebaseFeedTimerData` - Feeding tracking data
- `FirebaseHealthDocumentData` - Health tracking data
- `FirebaseGrowthData` - Growth measurements

## Architecture

This library uses:
- **Firebase Firestore Python SDK** - Official Google Cloud SDK (not REST API)
- **gRPC over HTTP/2** - Real-time communication protocol
- **Protocol Buffers** - Efficient data encoding
- **Firebase Authentication** - Secure token-based auth

### Why Not REST API?

Huckleberry's Firebase Security Rules block non-SDK requests. Direct REST API calls return `403 Forbidden`. This library uses the official Firebase SDK which uses gRPC, the same protocol as the Huckleberry mobile app.

## Requirements

- Python 3.14+
- `google-cloud-firestore>=2.11.0`
- `aiohttp>=3.10.0`
- `pydantic>=2.10.0`

## Development

### Running Tests

Integration tests require Huckleberry account credentials:

```bash
# Set environment variables
$env:HUCKLEBERRY_EMAIL = "test@example.com"
$env:HUCKLEBERRY_PASSWORD = "test-password"

# Run tests
.\run-tests.ps1
```

### Linting, Formatting, and Type Checking

```bash
# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run ty check --python-version 3.14 --ignore unknown-argument

# Add release notes about your feature
uv run towncrier create

# Build release notes from fragments when cutting a release
uv run towncrier build --yes --version <version>
```

See [tests/README.md](tests/README.md) for detailed testing documentation.

### CI/CD

- PR Validation runs on pull requests and pushes to `main` and checks Ruff linting/formatting and Ty type checking.
- Integration tests run on GitHub Actions and require Huckleberry credentials.

## License

MIT License

## Disclaimer

This is an unofficial, reverse-engineered API client. It is not affiliated with, endorsed by, or connected to Huckleberry Labs Inc. Use at your own risk.

## Related Projects

- [Huckleberry Home Assistant Integration](https://github.com/Woyken/huckleberry-homeassistant) - Home automation integration using this library
