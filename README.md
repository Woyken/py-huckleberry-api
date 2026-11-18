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
- 🗂️ **Entry Management**: List history with ids, edit entries, delete entries
- 🤖 **MCP Server**: Optional remote MCP server for Claude custom connectors

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

## MCP Server

```
Claude (web, desktop, mobile) --HTTPS--> Tailscale Funnel --> huckleberry-mcp (127.0.0.1) --gRPC--> Huckleberry Firebase
                                              sign-in: Clerk (Google)
```

| Part | Choice | Why |
|---|---|---|
| Server | [FastMCP](https://gofastmcp.com) over stateless Streamable HTTP at `/mcp` | One module, no custom protocol code; no sessions to expire or lose on restart |
| Sign-in | [Clerk](https://clerk.com) OAuth through FastMCP's `ClerkProvider` | Claude connectors sign in with OAuth; Clerk supplies Google sign-in and invite-only access |
| Tunnel | [Tailscale Funnel](https://tailscale.com/kb/1223/funnel) | Stable public HTTPS URL, no domain or open port |

The server signs in to Huckleberry with one account. Everyone who connects writes as that account.

### 1. Install

```bash
git clone https://github.com/Woyken/py-huckleberry-api
cd py-huckleberry-api
uv sync --extra mcp
```

### 2. Configure

Copy `.env.example` to `.config/.env` in the clone, run `chmod 600` on it, and fill it in. Git ignores `.config/`.
The server loads this file from its working directory. Environment variables that are already set take priority.

| Variable | Meaning |
|---|---|
| `HUCKLEBERRY_EMAIL`, `HUCKLEBERRY_PASSWORD` | Huckleberry account the server writes as |
| `HUCKLEBERRY_TIMEZONE` | IANA timezone for times without an offset, for example `Australia/Sydney` |
| `HUCKLEBERRY_MCP_HOST`, `HUCKLEBERRY_MCP_PORT` | Listen address, default `127.0.0.1:8765` |
| `HUCKLEBERRY_MCP_BASE_URL` | Public origin of the tunnel, without a path |
| `CLERK_DOMAIN`, `CLERK_OAUTH_CLIENT_ID`, `CLERK_OAUTH_CLIENT_SECRET` | Clerk OAuth application |
| `HUCKLEBERRY_MCP_ALLOWED_EMAILS` | Comma-separated emails allowed to use the tools. Required with Clerk |
| `HUCKLEBERRY_MCP_NO_AUTH` | Set to `1` to serve HTTP without sign-in. For local testing only |

The server refuses to start in HTTP mode without the Clerk variables and the email list, unless `HUCKLEBERRY_MCP_NO_AUTH=1`.
A signed-in user whose email is not in the list sees no tools.

### 3. Clerk

In the Clerk Dashboard, for a development instance:

1. **User authentication > Access mode**: select Invite-only, then invite each family member under **Users**.
2. **SSO connections > Add connection > For all users**: select Google. Development instances need no Google Cloud setup.
3. **OAuth applications > Add application**: scopes `openid email profile`, redirect URI `<HUCKLEBERRY_MCP_BASE_URL>/auth/callback`, entered exactly, with no trailing slash. Copy the client ID and the client secret. Clerk shows the secret once.

`CLERK_DOMAIN` is the instance host, for example `your-instance.clerk.accounts.dev`.

### 4. Run

Set `WorkingDirectory` to your clone.

```ini
# ~/.config/systemd/user/huckleberry-mcp.service
[Unit]
Description=Huckleberry MCP server
After=network-online.target

[Service]
WorkingDirectory=%h/py-huckleberry-api
ExecStart=%h/.local/bin/uv run --extra mcp huckleberry-mcp
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now huckleberry-mcp
loginctl enable-linger "$USER"        # keep it running after logout
tailscale funnel --bg 8765            # public https://<machine>.<tailnet>.ts.net -> 127.0.0.1:8765
```

Funnel must be enabled for the tailnet. The first `tailscale funnel` run prints a link to enable it.

For a local MCP client, run `uv run --extra mcp huckleberry-mcp --stdio`. No sign-in applies.

### 5. Connect Claude

1. On claude.ai, open **Settings > Connectors > Add custom connector**.
2. Enter `<HUCKLEBERRY_MCP_BASE_URL>/mcp` and add it.
3. Select **Connect** and sign in with Google.

The connector then appears in the Claude mobile app for the same account. Each family member repeats these steps
in their own Claude account. Each must be invited in Clerk and listed in `HUCKLEBERRY_MCP_ALLOWED_EMAILS`.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Sign-in fails with "redirect_uri ... does not match" | The Clerk OAuth application does not list `<HUCKLEBERRY_MCP_BASE_URL>/auth/callback`. Add it exactly |
| Connected, but Claude shows no tools | The signed-in email is not in `HUCKLEBERRY_MCP_ALLOWED_EMAILS` |
| Service exits with "Refusing to start" | A Clerk variable, the base URL, or the email list is missing. The message names it |
| A change to `.config/.env` has no effect | Restart: `systemctl --user restart huckleberry-mcp` |
| Claude cannot reach the server just after Funnel is enabled | Funnel can take a few minutes to reach every public address. Try again |

Logs: `journalctl --user -u huckleberry-mcp -f`. A failed tool call logs its reason as `Error in tools/call: ...`.

### Tools

| Tool | Purpose |
|---|---|
| `list_children`, `get_status` | Children; running timers, latest entry of each kind, local time |
| `sleep_timer`, `log_sleep` | Start, pause, resume, cancel, complete the sleep timer; log a finished sleep |
| `nursing_timer`, `log_nursing`, `log_bottle` | Breastfeeding timer with side switch; log nursing or a bottle |
| `pump_timer`, `log_pump` | Pump timer; log a pump session |
| `activity_timer`, `log_activity` | Activity timers (bath, tummy time and so on); log an activity |
| `list_foods`, `create_custom_food`, `log_solids` | Find or add foods; log a solids meal |
| `log_diaper`, `log_potty` | Diaper change or potty event |
| `log_growth`, `log_temperature` | Weight, height, head; body temperature |
| `list_medicine_types`, `create_medicine_type`, `log_medicine` | Medicines and doses |
| `list_entries`, `update_entry`, `delete_entry` | History with ids; edit or delete an entry |

Times are ISO 8601. A time without an offset is in `HUCKLEBERRY_TIMEZONE`. An omitted time means now.

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
- `await start_sleep(child_uid, start_time=None)` - Start sleep session; on an active timer, moves its start instead of restarting
- `await pause_sleep(child_uid)` - Pause active session
- `await resume_sleep(child_uid)` - Resume paused session
- `await cancel_sleep(child_uid)` - Cancel without saving
- `await complete_sleep(child_uid, start_time=None, end_time=None, details=None)` - Complete and save to history with optional interval overrides
- `await log_sleep(child_uid, start_time=..., end_time=..., details=None)` - Log a completed sleep interval with explicit timestamps

### Feeding Tracking
- `await get_nursing(child_uid)` - Get the typed feeding document and current nursing timer state
- `await start_nursing(child_uid, side, start_time=None)` - Start breastfeeding session; on an active session, shifts its start
- `await pause_nursing(child_uid)` - Pause active session
- `await resume_nursing(child_uid, side)` - Resume paused session
- `await switch_nursing_side(child_uid)` - Switch left/right
- `await cancel_nursing(child_uid)` - Cancel without saving
- `await complete_nursing(child_uid, start_time=None, end_time=None)` - Complete and save to history with optional interval overrides
- `await log_nursing(child_uid, start_time=..., end_time=..., side="left", notes=None)` - Log a completed breastfeeding interval with explicit timestamps
- `await log_bottle(child_uid, start_time=..., amount=..., bottle_type=..., units=..., notes=None)` - Log bottle feeding with an explicit event timestamp
  - `bottle_type`: "Breast Milk", "Formula", "Cow Milk", "Soy Milk", etc.
  - `amount`: Volume fed (e.g., 120.0)
  - `units`: "ml" or "oz"

### Pumping Tracking
- `await start_pump(child_uid, start_time=None)` - Start a pumping timer; on an active timer, moves its start
- `await pause_pump(child_uid)` / `await resume_pump(child_uid)` - Pause or resume pumping
- `await complete_pump(child_uid, start_time=None, end_time=None, total_amount=..., units=..., notes=...)` - Save the active timer with total or left/right amounts and optional interval overrides
- `await cancel_pump(child_uid)` - Cancel the active timer without saving
- `await log_pump(child_uid, start_time=..., total_amount=..., duration=..., units=...)` - Log pumping entry; total entries are stored split evenly across `leftAmount` and `rightAmount`
- `await list_pump_intervals(child_uid, start_time, end_time)` - List pump history for a range using `datetime` objects

### Activity Tracking
- `await start_activity(child_uid, mode, start_time=None)` - Start an activity timer; on the same active mode, moves its start
- `await pause_activity(child_uid, mode)` / `await resume_activity(child_uid, mode)` - Pause or resume an activity timer
- `await switch_activity(child_uid, mode)` - Associate the active timer with another activity type
- `await complete_activity(child_uid, mode, start_time=..., end_time=..., duration=..., notes=...)` - Save the active timer with optional edit-form overrides (`end_time` and `duration` are mutually exclusive)
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

### Entry Management
`tracker` is one of `"sleep"`, `"feed"`, `"diaper"`, `"health"`, `"pump"`, `"activities"`.
- `await get_tracker(child_uid, tracker)` - Get the typed tracker document (timer and `prefs`)
- `await list_entries(child_uid, tracker, start_time, end_time)` - List history as `HistoryEntry` objects with ids
- `await get_entry(child_uid, tracker, entry_id)` - Get one entry by id
- `await update_entry(child_uid, tracker, entry_id, changes)` - Change Firebase fields of an entry; a `None` value removes a field
- `await delete_entry(child_uid, tracker, entry_id)` - Delete an entry

`update_entry` and `delete_entry` rebuild the `prefs.last*` summaries from the latest remaining entries, so the app's "last" cards stay correct.

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

### Package layout

Modules prefixed with `_` are internal implementation. The supported surface is
`HuckleberryAPI`, `firebase_types.py`, and `models.py`.

| Module | Responsibility |
|---|---|
| `api.py` | `HuckleberryAPI`, composed from the feature mixins (public) |
| `firebase_types.py`, `models.py` | Firebase payload schemas and API input models (public) |
| `_client.py` | Authentication, Firestore clients, timezone |
| `_account.py` | Child and user profile reads |
| `_sleep.py`, `_feeding.py`, `_solids.py`, `_diaper.py`, `_health.py`, `_pump.py`, `_activity.py` | Tracker features |
| `_history.py` | Date-range history queries |
| `_entries.py` | History with ids, entry edit and delete |
| `mcp_server.py` | MCP server (`huckleberry-mcp`, needs the `mcp` extra) |
| `_listeners.py` | Real-time snapshot listeners |
| `_const.py`, `_common.py` | Constants and shared internals |

### Why Not REST API?

Huckleberry's Firebase Security Rules block non-SDK requests. Direct REST API calls return `403 Forbidden`. This library uses the official Firebase SDK which uses gRPC, the same protocol as the Huckleberry mobile app.

## Requirements

- Python 3.14+
- `google-cloud-firestore>=2.11.0`
- `aiohttp>=3.10.0`
- `pydantic>=2.10.0`
- `fastmcp>=4.2.0` for the MCP server (`mcp` extra)

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
