# AI Agent Context Guide - Huckleberry API

## Project Overview

This is a **Python API client library** for the Huckleberry baby tracking app. It provides programmatic access to Huckleberry's Firebase backend using the official Google Cloud Firestore SDK.

**Critical Context**: Huckleberry does NOT use REST APIs. It uses the Firebase Android SDK with gRPC over HTTP/2 and Protocol Buffers. This was the key discovery that enabled this project.

## Related Documentation

- **[Project Overview](../AGENTS.md)** - High-level context, history, decompiled source analysis
- **[Home Assistant Integration](../huckleberry-homeassistant/AGENTS.md)** - How this API is used in Home Assistant

## Project Purpose

This standalone package provides:
- Firebase authentication for Huckleberry accounts
- Sleep, feeding, diaper, and growth tracking
- Real-time listeners via Firestore snapshots
- Type-safe data structures (TypedDict)
- Reusable across any Python project (not just Home Assistant)

## Project History & Key Discoveries

### Initial Approach (Failed)
- **Method**: Analyzed HAR file from HTTP Toolkit showing Firebase REST API calls
- **Attempt**: Used REST API endpoints directly with Bearer token authentication
- **Result**: All requests returned `403 Forbidden` errors
- **Root Cause**: Firebase Security Rules block non-SDK requests

### Breakthrough Discovery
- **Analysis**: Decompiled Android APK using JADX
- **Finding**: `FirebasePlugin.java` line 185 shows `FirebaseFirestore.getInstance()`
- **Revelation**: App uses Firebase Android SDK (native gRPC), not REST API
- **Solution**: Use `google-cloud-firestore` Python SDK with Firebase Authentication

### Current Implementation
- **Protocol**: gRPC over HTTP/2 with Protocol Buffers encoding
- **SDK**: Official Google Cloud Firestore Python library
- **Auth**: Firebase Identity Toolkit API (identitytoolkit.googleapis.com)
- **Real-time**: Firestore snapshot listeners via `on_snapshot()`
- **Latency**: 0.2-1.0 seconds measured (< 1 second typical)

## Architecture

### Technology Stack

**Backend:**
- Firebase Firestore (project: `simpleintervals`)
- Firebase Authentication (email/password)
- gRPC protocol (not REST)

**Library:**
- Python 3.9+
- `google-cloud-firestore>=2.11.0` (required dependency)
- `requests>=2.31.0` (for Firebase Auth API)
- Type hints with TypedDict

### Firebase Configuration

```python
PROJECT_ID = "simpleintervals"
API_KEY = "AIzaSyApGVHktXeekGyAt-G6dIeWHUkq2oXqcjg"
APP_ID = "1:219218185774:android:a3e215cc246b92b0"
AUTH_ENDPOINT = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
FIRESTORE_ENDPOINT = "firestore.googleapis.com"  # gRPC, not REST
```

**Source**: `jadx output/resources/res/values/strings.xml` lines 97-108 (from APK decompilation)

### APK Decompilation for Research

To discover new features or verify data structures, decompile the Huckleberry APK:

**Tools Required**:
- [apkeep](https://github.com/EFForg/apkeep) - Download APKs from Google Play
- [JADX](https://github.com/skylot/jadx) - Decompile APK to Java/resources

**Download APK**:
```bash
# Install apkeep (requires Rust/Cargo)
cargo install apkeep

# Download specific version (recommended: 0.9.258 - last unobfuscated)
apkeep -a com.huckleberry_labs.app -v 0.9.258 .

# Download latest version
apkeep -a com.huckleberry_labs.app .
```

**Decompile with JADX**:
```bash
# GUI mode (recommended for exploration)
jadx-gui com.huckleberry_labs.app_0.9.258.apk

# Command line mode (for automation)
jadx -d "jadx output" com.huckleberry_labs.app_0.9.258.apk
```

**Important Notes**:
- **Version 0.9.258 is the last version with readable (unobfuscated) source code**
- Versions 0.9.280+ have obfuscated sources (class names like `a.b.c`)
- Always keep 0.9.258 decompilation for reference
- Compare newer versions only for API endpoint/structure changes
- Focus on AndroidManifest.xml and resources for newer versions

**Analysis Approach**:
1. **Java sources** (`sources/` directory): Backend operations, Firebase SDK calls, data structures
2. **JavaScript sources** (`resources/assets/www/`): UI logic, user interactions, display formats
3. **strings.xml** (`resources/res/values/`): Configuration values, API endpoints, Firebase config
4. **AndroidManifest.xml** (`resources/`): App permissions, services, activities

**Post-Decompilation Formatting**:

Format JavaScript files after decompilation for readability:
```bash
npx prettier --write --ignore-path "" "jadx output/resources/assets/www/*.js"
```

This reformats minified/webpacked JavaScript into human-readable code.

**Known Limitations**:
- Security rules not visible in decompiled code
- Server-side Cloud Functions not accessible
- Cordova plugins bridge Java ↔ JavaScript

### Firestore Collections

**Verified Collections** (Live testing confirmed):
1. **`users/{uid}`**: User profile, child list, lastChild reference
2. **`childs/{child_id}`**: Child metadata (name, birthday, settings, celebrations)
3. **`sleep/{child_uid}`**: Sleep timer and preferences
4. **`sleep/{child_uid}/intervals`**: Sleep history (subcollection)
5. **`feed/{child_uid}`**: Feeding timer and preferences
6. **`feed/{child_uid}/intervals`**: Feeding history (subcollection)
7. **`diaper/{child_uid}`**: Diaper preferences
8. **`diaper/{child_uid}/intervals`**: Diaper change history (subcollection)
9. **`health/{child_uid}`**: Health/growth preferences
10. **`health/{child_uid}/data`**: Growth measurements (subcollection)

**Discovered Collections** (From decompiled sources, December 2025):
11. **`insights/{child_uid}`**: AI-powered insights and recommendations
   - `insights/{child_uid}/dailyTips`: Daily parenting tips subcollection
   - `insights/{child_uid}/miniPlans`: Mini-plans subcollection
12. **`types/{child_uid}`**: Custom activity type definitions
    - `types/{child_uid}/custom`: User-created custom types subcollection
13. **`notifications/{uid}`**: User notifications (push/in-app)
    - `notifications/{uid}/messages`: Notification messages subcollection
14. **`recommendations`**: Global system-wide recommendations (not child-specific)

**Likely Collections** (Code references confirmed in latest output, December 2025):
15. **`pump/{child_uid}`**: Pumping/expressing milk tracking
    - `pump/{child_uid}/intervals`: Pumping history subcollection
16. **`potty/{child_uid}`**: Potty training tracking (part of diaper system)
    - `potty/{child_uid}/intervals`: Potty history subcollection
17. **`solids/{child_uid}`**: Solid food introduction tracking
    - `solids/{child_uid}/intervals`: Solids history subcollection
18. **`activities/{child_uid}`**: General activities (tummy time, play, etc.)
    - `activities/{child_uid}/intervals`: Activities history subcollection

**Security Model**: Documents accessed by UID/child_id directly. Collection-wide queries blocked by security rules.

**CRITICAL DISCOVERY - Subcollection Naming**:
- **Most trackers use `intervals` subcollection**: sleep, feed, diaper, pump, activities, solids
- **Health tracker uses `data` subcollection**: health/{child_uid}/data (NOT intervals!)
- **Source**: Decompiled JS (module 38473), `X9` mapping object
- **Implementation**: Must write to correct subcollection for entries to appear in app timeline

## Data Structure Critical Details

### Sleep Timer Structure

```javascript
{
  "timer": {
    "active": true,
    "paused": false,
    "timestamp": {"seconds": 1234567890.123},
    "timerStartTime": 1234567890123,  // MILLISECONDS (time.time() * 1000)
    "uuid": "16-char-hex",
    "details": {
      "sleepConditions": [],
      "sleepLocations": []
    }
  },
  "prefs": {
    "lastSleep": {
      "start": 1234567890,     // seconds
      "duration": 3600,        // seconds
      "offset": -120           // timezone minutes
    }
  }
}
```

**CRITICAL**: `timerStartTime` is in **MILLISECONDS** for sleep tracking.

### Feeding Timer Structure

```javascript
{
  "timer": {
    "active": true,
    "paused": false,
    "timestamp": {"seconds": 1234567890.123},  // Last update time
    "feedStartTime": 1234567890.0,     // SECONDS (not milliseconds!)
    "timerStartTime": 1234567890.0,    // SECONDS - resets on side switch/resume!
    "uuid": "16-char-hex",
    "leftDuration": 120.0,             // Accumulated seconds
    "rightDuration": 45.0,             // Accumulated seconds
    "lastSide": "none",                // Transitions to "none" when switching/resuming
    "activeSide": "left"               // CRITICAL: Current active side (home page uses this!)
  },
  "prefs": {
    "lastNursing": {
      "mode": "breast",
      "start": 1234567890.0,
      "duration": 165.0,               // Total (left + right)
      "leftDuration": 120.0,
      "rightDuration": 45.0,
      "offset": -120
    }
  }
}
```

**CRITICAL**:
- `timerStartTime` is in **SECONDS** for feeding (different from sleep!)
- `activeSide` field is what the home page uses to display current feeding time
- `timerStartTime` **resets to NOW** on side switch or resume
- `lastSide` becomes "none" during transitions

### Duration Accumulation (Feeding)

**How It Works**:
1. **On pause**: `elapsed = now - timerStartTime`, accumulate to `activeSide`, remove `activeSide` field
2. **On switch**: Accumulate to current side (if not paused), reset `timerStartTime = now`, set new `activeSide`, set `lastSide = "none"`
3. **On resume**: Reset `timerStartTime = now`, set `activeSide`, set `lastSide = "none"`
4. **On complete**: Accumulate final duration (if not paused), remove durations from timer, save to `lastNursing` and intervals

**Key Behaviors**:
- Switching sides **always resumes** (unpauses) the feeding
- Home page calculates: `now - timerStartTime` using `activeSide` to show current time
- Durations accumulate from `timerStartTime`, not from `timestamp`

**Implementation**: See `pause_feeding()`, `resume_feeding()`, `switch_feeding_side()`, and `complete_feeding()` in `api.py`

### Sleep Interval Structure

When completing sleep, create document in `sleep/{child_uid}/intervals`:

```javascript
{
  "_id": "auto-generated",
  "start": 1234567890,        // seconds
  "duration": 3600,           // seconds
  "offset": -120,             // timezone minutes
  "end_offset": -120,
  "details": {
    "sleepConditions": [],
    "sleepLocations": []
  },
  "lastUpdated": 1234567890
}
```

**Note**: Duration calculated as `(now - timerStartTime/1000)` for sleep.

### Feeding Interval Structure

When completing feeding, create document in `feed/{child_uid}/intervals`:

```javascript
{
  "mode": "breast",
  "start": 1234567890.0,      // seconds (feedStartTime)
  "lastSide": "right",        // Last side fed on
  "lastUpdated": 1234567890.0, // seconds
  "leftDuration": 120.0,      // seconds
  "rightDuration": 45.0,      // seconds
  "offset": -120.0,           // timezone minutes
  "end_offset": -120.0
}
```

**Document ID Format**: `{timestamp_ms}-{random_20_chars}` (e.g., `1764528069548-a04ff18de85c4a98a451`)

### Diaper Interval Structure

Diaper changes are logged as instant events (no timer) in `diaper/{child_uid}/intervals`:

```javascript
// Pee only
{
  "start": 1764589218.240,
  "lastUpdated": 1764589218.240,
  "mode": "pee",
  "offset": -120.0,
  "quantity": {"pee": 50.0}
}

// Poo only
{
  "start": 1764589218.605,
  "lastUpdated": 1764589218.605,
  "mode": "poo",
  "offset": -120.0,
  "quantity": {"poo": 100.0},
  "color": "yellow",      // "yellow", "green", "brown", "black", "red"
  "consistency": "soft"   // "runny", "soft", "solid", "hard"
}

// Both pee and poo
{
  "start": 1764589218.971,
  "lastUpdated": 1764589218.971,
  "mode": "both",
  "offset": -120.0,
  "quantity": {
    "pee": 50.0,
    "poo": 100.0
  },
  "color": "green",
  "consistency": "runny"
}

// Dry check
{
  "start": 1764589219.349,
  "lastUpdated": 1764589219.349,
  "mode": "dry",
  "offset": -120.0
  // No quantity field for dry checks
}
```

**Document ID Format**: Same as other intervals - `{timestamp_ms}-{random_20_chars}`

**Note**: Unlike sleep/feeding, diapers are instant events with no active/paused state. The `prefs.lastDiaper` field stores the most recent change.

### Growth Data Structure

Growth measurements logged in `health/{child_uid}/data`:

```javascript
{
  "_id": "timestamp-random",
  "type": "health",
  "mode": "growth",
  "start": 1234567890.0,
  "lastUpdated": 1234567890.0,
  "offset": -120.0,
  "isNight": false,
  "multientry_key": null,
  "weight": 5.2,
  "weightUnits": "kg",        // or "lbs"
  "height": 52.0,
  "heightUnits": "cm",        // or "in"
  "head": 35.0,
  "headUnits": "hcm"          // or "hin" (head inches)
}
```

**Note**: Health uses `data` subcollection, not `intervals` like other trackers.

## Code Organization

### File Structure

```
src/huckleberry_api/
├── __init__.py          # Package exports
├── api.py               # HuckleberryAPI class (main implementation)
├── types.py             # TypedDict definitions
├── const.py             # Firebase constants
└── py.typed             # Type hints marker
```

### Key Classes

**`HuckleberryAPI`** (`api.py`):
- Core Firebase operations
- Authentication with token refresh
- Sleep methods: `start_sleep`, `pause_sleep`, `resume_sleep`, `cancel_sleep`, `complete_sleep`
- Feeding methods: `start_feeding`, `pause_feeding`, `resume_feeding`, `switch_feeding_side`, `cancel_feeding`, `complete_feeding`
- Diaper methods: `log_diaper` (supports pee, poo, both, dry modes)
- Growth methods: `log_growth`, `get_growth_data`
- Real-time listeners: `setup_realtime_listener`, `setup_feed_listener`, `setup_health_listener`, `stop_all_listeners`
- Helper: `get_children()` - retrieves child from `users/{uid}.lastChild`

**`FirebaseTokenCredentials`** (`api.py`):
- Custom credentials class for Firebase SDK
- Implements `google.auth.credentials.Credentials` interface
- Sets `token` attribute for gRPC authentication

### Type Definitions (`types.py`)

- `ChildData` - Child profile structure
- `SleepDocumentData` / `SleepTimerData` - Sleep tracking
- `FeedDocumentData` / `FeedTimerData` - Feeding tracking
- `HealthDocumentData` - Health tracking
- `GrowthData` - Growth measurements

## Critical Implementation Rules

### 1. Timer Time Formats

**Sleep**: `timerStartTime` in **milliseconds**
```python
timer_start_ms = time.time() * 1000
```

**Feeding**: `timerStartTime` in **seconds**
```python
timer_start = time.time()
```

**Reason**: Unknown why formats differ, but verified through app testing.

### 2. Duration Calculation

**Sleep**: Direct calculation from timerStartTime
```python
duration = (now - timer_start_ms / 1000)
```

**Feeding**: Accumulate from timerStartTime (resets on switch/resume)
```python
# timerStartTime resets on every side switch and resume
elapsed = now - timer_start_time  # timerStartTime in seconds
if current_side == "left":
    left_duration += elapsed
else:
    right_duration += elapsed
```

**Key Difference**: Feeding uses `timerStartTime` (which resets), not `timestamp`

### 3. Firestore Update Patterns

**Overwrite entire object**: Use dict
```python
feed_ref.update({"timer": {...}})
```

**Update specific fields**: Use field paths
```python
feed_ref.update({"timer.paused": True})
```

**Preserve existing data**: Use field paths (critical for pause/resume)
```python
# GOOD - preserves timerStartTime
feed_ref.update({"timer.paused": False, "timer.active": True})

# BAD - overwrites entire timer, loses timerStartTime
feed_ref.update({"timer": {"paused": False, "active": True}})
```

### 4. Cancel vs Complete

**Cancel**: Set `timer.active = False` (preserve structure, don't delete)
```python
feed_ref.update({"timer.active": False})
```

**Complete**: Set inactive + save to history
```python
feed_ref.update({
    "timer.active": False,
    "prefs.lastNursing": {...}
})
```

**Reason**: App expects inactive timer structure, not missing timer.

### 5. Real-time Listener Cleanup

Always unsubscribe listeners:
```python
# Store reference
self._listener = doc_ref.on_snapshot(callback)

# Later, cleanup
if self._listener:
    self._listener.unsubscribe()  # Call method, don't invoke as callable
```

### 6. UUID Generation

Use 16-character hex string:
```python
uuid.uuid4().hex[:16]
```

**Not**: Full UUID, not uppercase, not dashed format.

### 7. Child Birthday Field Name

**Wrong**:
```python
child_data.get("birthday")  # Returns None
```

**Right**:
```python
child_data.get("birthdate")  # Returns "2025-02-22"
```

**Reason**: Firestore field is named `birthdate` (not `birthday`) and stores date as string in YYYY-MM-DD format.

## Common Pitfalls & Solutions

### Pitfall 1: Using REST API

**Wrong**:
```python
headers = {"Authorization": f"Bearer {token}"}
requests.get(f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents/sleep/{uid}")
```

**Result**: 403 Forbidden (Security Rules block non-SDK requests)

**Right**:
```python
client = firestore.Client(project=project, credentials=credentials)
doc_ref = client.collection("sleep").document(uid)
doc = doc_ref.get()
```

### Pitfall 2: Milliseconds vs Seconds

**Wrong** (feeding):
```python
timer_start_ms = time.time() * 1000  # Milliseconds
feed_ref.update({"timer": {"timerStartTime": timer_start_ms}})
```

**Result**: App timer shows 0:00 or incorrect time

**Right**:
```python
timer_start = time.time()  # Seconds
feed_ref.update({"timer": {"timerStartTime": timer_start}})
```

### Pitfall 3: Overwriting Timer on Pause

**Wrong**:
```python
feed_ref.update({"timer": {"paused": True, "active": True}})
```

**Result**: Loses `timerStartTime`, `uuid`, durations

**Right**:
```python
feed_ref.update({"timer.paused": True, "timer.active": True})
```

### Pitfall 4: Missing activeSide Field

**Wrong**:
```python
# Start feeding without activeSide
feed_ref.update({
    "timer": {
        "active": True,
        "lastSide": "right",
        "timerStartTime": now
    }
})
```

**Result**: Home page shows 0:00 because it looks for `activeSide`, not `lastSide`

**Right**:
```python
feed_ref.update({
    "timer": {
        "active": True,
        "activeSide": "right",  # Home page uses this!
        "lastSide": "left",     # This is different
        "timerStartTime": now
    }
})
```

### Pitfall 5: Health Tracker Uses Different Subcollection

**Wrong**:
```python
# Trying to create growth entry in intervals subcollection (like other trackers)
health_ref = client.collection("health").document(child_uid)
interval_ref = health_ref.collection("intervals").document(interval_id)
interval_ref.set(growth_entry)
```

**Result**: 403 Forbidden error from Firebase Security Rules

**Right**:
```python
# Health uses "data" subcollection, not "intervals"
health_ref = client.collection("health").document(child_uid)
data_ref = health_ref.collection("data").document(interval_id)
data_ref.set(growth_entry)
```

**Reason**: All other trackers (sleep, feed, diaper, pump, activities, solids) use `intervals` subcollection. Health tracker uniquely uses `data` subcollection.

### Pitfall 6: Missing Document on Start (Update vs Set)

**Wrong**:
```python
# Fails if document doesn't exist (e.g. new child or cleared data)
sleep_ref.update({"timer": {...}})
```

**Result**: `google.api_core.exceptions.NotFound: 404 No document to update`

**Right**:
```python
# Creates document if missing, merges if exists
sleep_ref.set({"timer": {...}}, merge=True)
```

## Future Development Guidelines

### Adding New Activity Types

Template for adding bottle feeding, pump tracking, potty training, etc.:

1. **Check decompiled sources** for data structure in relevant collection
2. **Create test script** to verify structure with live account
3. **Add methods to `api.py`**: start, pause, resume, cancel, complete (if applicable)
4. **Add type definitions** to `types.py` for new data structures
5. **Update documentation** (README.md, DATA_STRUCTURE.md)
6. **Write tests** to validate new functionality
7. **Update version** in pyproject.toml

### Extending Existing Features

**History Analysis**:
- Read from `{tracker}/{uid}/intervals` or `health/{uid}/data` subcollections
- Calculate averages, trends, patterns
- Provide statistical analysis methods

**Timezone Support**:
- Currently hardcoded: `offset: -120`
- Accept timezone parameter in methods
- Convert user timezone to offset minutes
- Apply to all interval/history writes

**Multiple Children Support**:
- Current: Methods accept `child_uid` parameter
- Enhancement: Add methods to list all children
- Batch operations across multiple children

### Code Style & Conventions

**Naming**:
- Classes: `PascalCase` (e.g., `HuckleberryAPI`)
- Functions: `snake_case` (e.g., `start_feeding`)
- Private methods: `_leading_underscore` (e.g., `_get_firestore_client`)
- Constants: `UPPER_CASE` (e.g., `PROJECT_ID`, `API_KEY`)

**Type Hints**:
- All public methods must have type hints
- Use TypedDict for structured data
- Use Optional[] for nullable values
- Use Union[] or | for multiple types

**Documentation**:
- All public methods need docstrings
- Include parameter descriptions
- Document exceptions raised
- Provide usage examples

**Error Handling**:
```python
try:
    doc = doc_ref.get()
    if not doc.exists:
        raise ValueError(f"Document not found: {doc_ref.id}")
except Exception as err:
    raise RuntimeError(f"Failed to fetch document: {err}") from err
```

## Testing Strategy

### Integration Tests

**Location**: `tests/` directory with modular test structure

**Test Files**:
- `tests/conftest.py` - Shared fixtures (api, child_uid)
- `tests/test_authentication.py` - Auth, token refresh, children, error handling (6 tests)
- `tests/test_sleep.py` - Sleep tracking and interval creation (3 tests)
- `tests/test_feeding.py` - Feeding tracking, side switching, intervals (4 tests)
- `tests/test_diaper.py` - Diaper logging (pee, poo, both, dry) (4 tests)
- `tests/test_growth.py` - Growth measurements (metric, imperial) (3 tests)
- `tests/test_listeners.py` - Real-time listeners and token refresh (3 tests)

**Requirements**:
```bash
# Environment variables required
HUCKLEBERRY_EMAIL="test@example.com"
HUCKLEBERRY_PASSWORD="test-password"
```

**Running Tests**:
```bash
# All tests
uv run pytest tests/ -v

# Specific module
uv run pytest tests/test_sleep.py -v

# Specific test
uv run pytest tests/test_sleep.py::TestSleepTracking::test_start_and_cancel_sleep -v
```

**CI/CD**: GitHub Actions workflow runs all tests on push to `main` branch

### Local Testing

Create test scripts to validate:
- Firebase SDK authentication
- Collection access patterns
- Real-time listener latency
- Sleep/feeding/diaper/growth operations

**Example Test**:
```python
from huckleberry_api import HuckleberryAPI

api = HuckleberryAPI(email="test@example.com", password="password", timezone="Europe/London")
api.authenticate()
children = api.get_children()
child_uid = children[0]["uid"]

# Test sleep tracking
api.start_sleep(child_uid)
time.sleep(5)
api.complete_sleep(child_uid)
```

### Real-time Listener Testing

Validate bidirectional sync with Huckleberry app:
1. Start tracking in Python
2. Verify appears in app
3. Pause in app
4. Verify listener receives update
5. Complete in Python
6. Verify appears in app history

## Debugging Tips

### Common Issues

**Authentication Failures**:
```python
# Check credentials
api = HuckleberryAPI(email="test@example.com", password="password", timezone="Europe/London")
try:
    api.authenticate()
    print("Authentication successful")
except Exception as e:
    print(f"Auth failed: {e}")
```

**Connection Issues**:
- Verify internet connectivity
- Check firewall settings for gRPC (port 443)
- Ensure Firestore endpoint is reachable

**Data Not Syncing**:
- Verify listener is properly registered
- Check callback function is being invoked
- Ensure proper child_uid is used
- Validate document path is correct

**Timer Issues**:
- Sleep uses milliseconds, feeding uses seconds
- Always use field paths for partial updates
- Never delete timer objects, set `active: False`

### Logging Best Practices

Add logging to your application:
```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("huckleberry_api")

# API will log operations
api = HuckleberryAPI(email="...", password="...", timezone="Europe/London")
api.authenticate()
```

## Additional Resources

### Documentation Files

- **README.md**: User-facing documentation and quick start
- **DATA_STRUCTURE.md**: Complete Firestore schema reference
- **AGENTS.md**: This file - AI agent context guide

### Example Scripts

Create test scripts to validate functionality:
- Authentication and token refresh testing
- Sleep tracking complete workflow
- Feeding side switching and duration accumulation
- Diaper logging with all modes
- Growth tracking with unit conversion
- Real-time listener latency measurement

### External References

- [Firebase Python SDK Documentation](https://googleapis.dev/python/firestore/latest/)
- [Google Cloud Firestore](https://cloud.google.com/firestore/docs)
- [gRPC Python](https://grpc.io/docs/languages/python/)
- [Protocol Buffers](https://developers.google.com/protocol-buffers)

## Dependencies & Compatibility

### Required Dependencies

```toml
[project]
requires-python = ">=3.9"
dependencies = [
    "google-cloud-firestore>=2.11.0",
    "requests>=2.31.0",
]
```

**Why cloud_push pattern**:
- Real-time snapshot listeners provide instant updates
- No polling interval needed
- Push notifications from Firestore via gRPC

**Transitive Dependencies**:
- Firebase SDK includes `grpcio`, `google-auth`, `protobuf` (auto-installed)

### Python Version

**Minimum**: 3.9+
- Uses modern type hints (TypedDict, | union syntax)
- Compatible with 3.9, 3.10, 3.11, 3.12+

## Performance Considerations

### Real-time Listeners

**Overhead**:
- One persistent gRPC connection per listener
- Minimal bandwidth (Protocol Buffers)
- Typical: 2-3 listeners per child (sleep, feed, health)

**Scaling**:
- 5 children = 10-15 listeners
- Python handles async efficiently
- No polling overhead

### Memory Usage

- API instance stores credentials and listeners
- Typical: ~5KB per child data
- Firestore client: ~10MB overhead

### Network Reliability

- Listeners auto-reconnect on disconnect
- No data loss on reconnection
- Offline behavior: Methods raise exceptions

## Security Considerations

### Credentials Storage

- API accepts email/password in constructor
- Token stored in memory (1-hour lifetime)
- Automatic token refresh before expiry
- No persistent credential storage by library

### API Key Exposure

- API key hardcoded in library (public in decompiled app)
- Key is **client-side**, not secret
- Security enforced by Firebase Auth + Security Rules
- User must have valid account credentials

### Network Security

- All traffic over TLS (HTTPS/gRPC)
- Firebase handles encryption
- No third-party data transmission

## Version Management

### Semantic Versioning

Follow [semver.org](https://semver.org/):

- **MAJOR** (1.0.0): Breaking API changes
  - Method signatures changed
  - Return types modified
  - Required parameters added

- **MINOR** (0.2.0): New features, backwards compatible
  - New methods added
  - Optional parameters added
  - New type definitions

- **PATCH** (0.1.1): Bug fixes, backwards compatible
  - Bug fixes
  - Documentation updates
  - Internal refactoring

### Updating Version

**CRITICAL**: Do NOT manually edit `pyproject.toml` to change the version. Always use `uv` to ensure consistency.

Use `uv` to bump:
```bash
uv version --bump patch   # 0.1.0 -> 0.1.1
uv version --bump minor   # 0.1.0 -> 0.2.0
uv version --bump major   # 0.1.0 -> 1.0.0
```

### GitHub Releases

Use GitHub CLI (`gh`) to control releases. **Do not use `--generate-notes`**. Instead, provide the release notes explicitly based on the `CHANGELOG.md` entry.

```bash
# Create release with explicit notes (copy from CHANGELOG.md)
gh release create v0.1.10 --notes "## Added
- Feature A
- Feature B

## Fixed
- Bug C"
```

## Maintaining This Guide

**CRITICAL FOR AI AGENTS**: This document must be kept up-to-date as the living source of truth for the API library.

### When to Update AGENTS.md

**ALWAYS update this file when you discover:**
1. **New data structures** in Firestore collections
2. **API behavior changes** or undocumented edge cases
3. **Critical bugs** and their solutions (add to Common Pitfalls)
4. **Implementation patterns** that work or fail
5. **Performance insights** from testing
6. **Security considerations** or authentication changes
7. **Breaking changes** in Firebase SDK or Python versions
8. **New decompiled source findings** from APK analysis

### Update Checklist

- [ ] New discovery documented with examples
- [ ] Related sections cross-referenced
- [ ] Code tested and verified
- [ ] Clear explanation of WHY it matters

**Remember**: Future AI agents and developers depend on this document for accurate context. Incomplete or incorrect information will propagate through all future work.

---

**Last Updated**: December 15, 2025
**Library Version**: 0.1.14
**Status**: Stable, feature-complete for sleep, feeding, diaper, and growth tracking
**Test Coverage**: 23 integration tests, CI/CD enabled, ~75 second runtime

## Recent Changes

See [CHANGELOG.md](CHANGELOG.md) for full version history.
