# Huckleberry API

[![Integration Tests](https://github.com/Woyken/py-huckleberry-api/actions/workflows/integration-tests.yml/badge.svg)](https://github.com/Woyken/py-huckleberry-api/actions/workflows/integration-tests.yml)

Python API client for the Huckleberry baby tracking app using Firebase Firestore.

## Overview

This is a reverse-engineered API client that connects directly to Huckleberry's Firebase backend using the official Google Cloud Firestore SDK. It provides programmatic access to baby tracking features including sleep, feeding, diaper changes, and growth measurements.

## Features

- 🔐 **Firebase Authentication**: Secure email/password authentication with automatic token refresh
- 💤 **Sleep Tracking**: Start, pause, resume, cancel, and complete sleep sessions
- 🍼 **Feeding Tracking**: Track breastfeeding with left/right side switching
- 🧷 **Diaper Changes**: Log pee, poo, both, or dry checks with color/consistency
- 📏 **Growth Measurements**: Record weight, height, and head circumference
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
from huckleberry_api import HuckleberryAPI

# Initialize API client
api = HuckleberryAPI(
    email="your-email@example.com",
    password="your-password",
    timezone="Europe/London"
)

# Authenticate
api.authenticate()

# Get children
children = api.get_children()
child_uid = children[0]["uid"]

# Start sleep tracking
api.start_sleep(child_uid)

# Complete sleep session
api.complete_sleep(child_uid)

# Start feeding
api.start_feeding(child_uid, side="left")

# Switch sides
api.switch_feeding_side(child_uid)

# Complete feeding
api.complete_feeding(child_uid)

# Log diaper change
api.log_diaper(child_uid, mode="both", pee=True, poo=True,
               color="yellow", consistency="soft")

# Log growth measurements
api.log_growth(child_uid, weight=5.2, height=52.0, head=35.0, units="metric")
```

## Real-time Listeners

Set up real-time listeners for instant updates:

```python
def on_sleep_update(data):
    timer = data.get("timer", {})
    print(f"Sleep active: {timer.get('active')}")
    print(f"Sleep paused: {timer.get('paused')}")

# Setup listener
api.setup_realtime_listener(child_uid, on_sleep_update)

# Stop all listeners when done
api.stop_all_listeners()
```

## API Methods

### Authentication
- `authenticate()` - Authenticate with Firebase
- `refresh_auth_token()` - Refresh expired token

### Children
- `get_children()` - Get list of children with profiles

### Sleep Tracking
- `start_sleep(child_uid)` - Start sleep session
- `pause_sleep(child_uid)` - Pause active session
- `resume_sleep(child_uid)` - Resume paused session
- `cancel_sleep(child_uid)` - Cancel without saving
- `complete_sleep(child_uid)` - Complete and save to history
- `complete_sleep(child_uid)` - Complete and save to history

### Feeding Tracking
- `start_feeding(child_uid, side)` - Start feeding session
- `pause_feeding(child_uid)` - Pause active session
- `resume_feeding(child_uid, side)` - Resume paused session
- `switch_feeding_side(child_uid)` - Switch left/right
- `cancel_feeding(child_uid)` - Cancel without saving
- `complete_feeding(child_uid)` - Complete and save to history

### Diaper Tracking
- `log_diaper(child_uid, mode, pee, poo, color, consistency)` - Log diaper change
  - `mode`: "pee", "poo", "both", or "dry"
  - `color`: "yellow", "green", "brown", "black", "red"
  - `consistency`: "runny", "soft", "solid", "hard"

### Growth Tracking
- `log_growth(child_uid, weight, height, head, units)` - Log measurements
  - `units`: "metric" (kg/cm) or "imperial" (lbs/inches)
- `get_growth_data(child_uid)` - Get latest measurements

### Real-time Listeners
- `setup_realtime_listener(child_uid, callback)` - Listen to sleep updates
- `setup_feed_listener(child_uid, callback)` - Listen to feeding updates
- `setup_health_listener(child_uid, callback)` - Listen to health updates
- `stop_all_listeners()` - Stop all active listeners

## Type Definitions

The package includes TypedDict definitions for type safety:

- `ChildData` - Child profile information
- `SleepDocumentData` / `SleepTimerData` - Sleep tracking data
- `FeedDocumentData` / `FeedTimerData` - Feeding tracking data
- `HealthDocumentData` - Health tracking data
- `GrowthData` - Growth measurements

## Architecture

This library uses:
- **Firebase Firestore Python SDK** - Official Google Cloud SDK (not REST API)
- **gRPC over HTTP/2** - Real-time communication protocol
- **Protocol Buffers** - Efficient data encoding
- **Firebase Authentication** - Secure token-based auth

### Why Not REST API?

Huckleberry's Firebase Security Rules block non-SDK requests. Direct REST API calls return `403 Forbidden`. This library uses the official Firebase SDK which uses gRPC, the same protocol as the Huckleberry mobile app.

## Requirements

- Python 3.9+
- `google-cloud-firestore>=2.11.0`
- `requests>=2.31.0`

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

See [tests/README.md](tests/README.md) for detailed testing documentation.

### CI/CD

Integration tests run automatically on GitHub Actions for all pushes to `main`. See [GITHUB_SECRETS_SETUP.md](GITHUB_SECRETS_SETUP.md) for instructions on configuring GitHub secrets.

## License

MIT License

## Disclaimer

This is an unofficial, reverse-engineered API client. It is not affiliated with, endorsed by, or connected to Huckleberry Labs Inc. Use at your own risk.

## Related Projects

- [Huckleberry Home Assistant Integration](https://github.com/Woyken/huckleberry-homeassistant) - Home automation integration using this library
