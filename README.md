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
- 🧸 **Activity Tracking**: Log activities and control timers, including switching activity type
- 🧷 **Diaper Changes**: Log pee, poo, both, or dry checks with color/consistency
- 📏 **Growth Measurements**: Record weight, height, and head circumference
- 💊 **Medicine Tracking**: Manage medicine types and record doses
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
        await api.log_temperature(
            child_uid,
            start_time=datetime.now(),
            amount=37.2,
            units="C",
        )

        medicine_type = await api.create_medicine_type(child_uid, "Vitamin D")
        await api.log_medicine(
            child_uid,
            start_time=datetime.now(),
            medicine_type=medicine_type,
            amount=1.0,
            units="ml",
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
- `await get_sleep(child_uid)` - Get the typed sleep document and current timer state
- `await start_sleep(child_uid)` - Start sleep session
- `await pause_sleep(child_uid)` - Pause active session
- `await resume_sleep(child_uid)` - Resume paused session
- `await cancel_sleep(child_uid)` - Cancel without saving
- `await complete_sleep(child_uid)` - Complete and save to history
- `await log_sleep(child_uid, start_time=..., end_time=..., details=None)` - Log a completed sleep interval with explicit timestamps

### Feeding Tracking
- `await get_nursing(child_uid)` - Get the typed feeding document and current nursing timer state
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
- `await start_pump(child_uid)` - Start a pumping timer
- `await pause_pump(child_uid)` / `await resume_pump(child_uid)` - Pause or resume pumping
- `await complete_pump(child_uid, total_amount=..., units=..., notes=...)` - Save the active timer with total or left/right amounts
- `await cancel_pump(child_uid)` - Cancel the active timer without saving
- `await log_pump(child_uid, start_time=..., total_amount=..., duration=..., units=...)` - Log pumping entry; total entries are stored split evenly across `leftAmount` and `rightAmount`
- `await list_pump_intervals(child_uid, start_time, end_time)` - List pump history for a range using `datetime` objects

### Activity Tracking
- `await start_activity(child_uid, mode)` - Start an activity timer
- `await pause_activity(child_uid, mode)` / `await resume_activity(child_uid, mode)` - Pause or resume an activity timer
- `await switch_activity(child_uid, mode)` - Associate the active timer with another activity type
- `await complete_activity(child_uid, mode, start_time=..., duration=..., notes=...)` - Save the active timer with optional edit-form overrides
- `await cancel_activity(child_uid, mode)` - Reset the active timer without saving
- `await log_activity(child_uid, mode=..., start_time=..., duration=..., notes=...)` - Save an activity directly without running a timer
- `await list_activity_intervals(child_uid, start_time, end_time)` - List activity history for a range

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

### Temperature Tracking
- `await log_temperature(child_uid, start_time=..., amount=..., units=..., notes=None)` - Log a body-temperature measurement
  - `units`: `"C"` for Celsius or `"F"` for Fahrenheit

### Medicine Tracking
- `await list_medicine_types(child_uid)` - List active child-specific medicine types
- `await create_medicine_type(child_uid, name)` - Add a selectable medicine type
- `await log_medicine(child_uid, start_time=..., medicine_type=..., amount=..., units=..., notes="")` - Log a medicine dose
  - `medicine_type`: A type returned by `list_medicine_types()` or `create_medicine_type()`
  - `amount`: Optional; the app-compatible blank value is stored as `0.0`
  - `units`: `"ml"`, `"oz"`, `"tsp"`, or `"drops"`

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

### Huckleberry app-control skill

Repository skills live in the shared `.agents/skills/` directory, rather than an agent-specific folder.
The existing [APK reverse-engineering skill](.agents/skills/huckleberry-apk-reverse/SKILL.md) is available there too.

The repository includes a [Huckleberry app-control agent skill](.agents/skills/controlling-huckleberry/SKILL.md)
for using the Android app as the reference implementation when investigating API/Firebase parity.
It provides one entry point with **explicit backend selection**: the shared Windows/Termux accessibility
controller for inspection, Home/Reports navigation and semantic node actions, or the preserved
Windows CDP implementation for its broader WebView/form/screenshot/checkpoint capabilities. These are documentation routes to existing CLIs,
not interchangeable drivers or a new `--backend` flag. No automatic fallback or mixed-driver investigation is supported.
It does not bundle Firebase credentials or a backend observer, and UI evidence alone does not establish parity.

Both routes require **Node.js 22.12+**. Accessibility additionally needs the matching repository tooling,
an explicitly enrolled private profile, and a manually consented bridge; Termux uses rish with a managed profile,
while ADB remains emulator-only. It exposes semantic click/set-text/scroll/focus/select/back actions and a
control catalog, but no screenshots or coordinate gestures; Save/delete/timer writes require an authorized
observer and captured before-state.
The [Windows accessibility live record](.agents/skills/controlling-huckleberry/references/windows-accessibility-live.md)
separately validates the supported workflow on an Android 14 emulator after manual consent,
including an explicitly built API-34 helper. It does not establish every Windows setup or broader bridge capabilities.

The CDP route requires **native Windows, an existing Android SDK/AVD, and an installed Huckleberry build exposing WebView debugging**.
Its `config.json` defaults to `huck34` / `emulator-5580`; configure an existing emulator before use.
The skill does not create/wipe AVDs or install/enable a bridge silently. Its CDP host claim and accessibility device claim
are independent; do not drive the same app with both or advertise unified locking.

For **Windows CDP** setup/offline tests, from the repository root:

```powershell
$skill = (Resolve-Path './.agents/skills/controlling-huckleberry').Path
node "$skill/scripts/huckleberry.mjs" setup
npm --prefix $skill test
```

`setup` installs the lockfile-pinned Playwright runtime under `%LOCALAPPDATA%/huckleberry-agent/runtime`,
outside the skill-discovery tree; it does not download a desktop browser.
The offline tests do not contact Android devices or Firebase. Follow the skill's selected-backend guide for live control,
and use its [parity protocol](.agents/skills/controlling-huckleberry/references/api-parity.md) before performing backend-changing experiments.

### Android accessibility debugging tools

[`tools/huckleberry-control/`](tools/huckleberry-control/README.md) contains a
shared Windows ADB / Termux Shizuku controller and a small Android accessibility
bridge for authorized app debugging. These are **developer tools only**, separate
from the Python API package and release product. No bridge APK or private signing
key is shipped in the Python wheel.

The scope is accessibility inspection, guarded Home/Reports navigation and
semantic node actions (click, long-click, set-text, scroll, focus, select, back),
with a machine-readable control catalog. There are no screenshots or coordinate
gestures, and Save/delete/timer actions require the documented parity safeguards
and an authorized observer. Build/install and accessibility consent are explicit;
read the tool's setup and target-safety instructions before use. The skill's
[accessibility route guide](.agents/skills/controlling-huckleberry/references/accessibility.md)
invokes this common controller without duplicating transport logic or changing
protocol 2.

The [opt-in Termux acceptance runner](tools/huckleberry-control/references/termux-acceptance.md)
checks an already-foreground enrolled managed-profile app. Its default is observation-only;
navigation requires explicit opt-in and retained action IDs. It never launches/prepares the app,
changes service consent, or auto-releases an uncertain run. A timed-out run plus operator recovery
is not a completed acceptance round-trip. Do not use it with an ADB/Windows profile.

The [recorded acceptance evidence](.agents/skills/controlling-huckleberry/references/acceptance-status.md)
distinguishes CI/build checks, historical controller tests and fresh runner results:
Termux observation passed, but fresh full navigation remained uncertain and was
explicitly reconciled/released. Separate fresh Windows/Android 14 acceptance passed its
supported workflow, including a clean cold-process launch after a guarded readback fix;
the default API-35 local build was not performed.
Neither successful CI nor that recovery establishes full device or Firebase parity.

```sh
npm --prefix tools/huckleberry-control test
node tools/huckleberry-control/cli.mjs --help
```

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
