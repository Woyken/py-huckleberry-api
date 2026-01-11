# Huckleberry Firestore Data Structure

## Overview
Based on live testing with Firebase SDK, here's the complete data structure needed for Home Assistant integration.

## Authentication
- **Endpoint**: `https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={API_KEY}`
- **Method**: POST with email/password
- **Returns**: `idToken` (JWT) valid for 1 hour, `refreshToken` for renewal

## Firestore Collections

### 1. `users/{uid}` - User Profile
**Purpose**: User account information and child references

**Key Fields**:
```javascript
{
  "email": "user@example.com",
  "firstname": "First",
  "lastname": "Last",
  "childList": ["child_id_1", "child_id_2"],  // Array of child IDs
  "lastChild": "child_id",                     // Currently selected child
  "subscription": {...},                       // Subscription status
  "onboarding_platform": "android",
  "isOnboardingCompleted": true
}
```

### 2. `feed/{uid}` - Feeding Tracking
**Purpose**: Active feeding timer and feeding history

**Structure**:
```javascript
{
  "timer": {
    "active": true/false,           // Is timer currently running?
    "paused": true/false,            // Is timer paused?
    "timestamp": {                   // When timer last updated (used for duration calc)
      "seconds": 1763556140.347
    },
    "feedStartTime": 1763555950.0,  // Absolute start time (seconds)
    "timerStartTime": 1763555950.0, // Timer start (seconds, NOT milliseconds like sleep)
    "uuid": "552cea39f0bf115a",      // Session ID
    "local_timestamp": 1763556140.347,
    "leftDuration": 0.0,             // Accumulated seconds fed from left breast
    "rightDuration": 190.0,          // Accumulated seconds fed from right breast
    "lastSide": "right"              // Which side currently feeding ("left" or "right")
  },
  "prefs": {
    "bottleType": "Breast Milk",
    "bottleAmount": 10.0,
    "bottleUnits": "ml",
    "lastNursing": {                 // Last completed nursing session
      "mode": "breast",
      "start": 1763541213.0,
      "duration": 1223.0,            // Total duration (leftDuration + rightDuration)
      "leftDuration": 1223.0,
      "rightDuration": 0.0,
      "offset": -120.0,              // Timezone offset in minutes
      "timestamp": 1763542436
    },
    "lastBottle": {                  // Last bottle feeding
      "mode": "bottle",
      "start": 1756546980.0,
      "bottleType": "Breast Milk",
      "bottleAmount": 10.0,
      "bottleUnits": "ml",
      "offset": -180.0
    },
    "lastSolid": {                   // Last solid food meal
      "mode": "solids",
      "start": 1763536533.961,
      "foods": {...},                // Dict of food items
      "reactions": {"LOVED": true},
      "notes": "",
      "offset": -120.0
    },
    "reminderV2": {...}              // Feeding reminders config
  }
}
```

**Key Implementation Notes**:
- **Duration Accumulation**: `leftDuration` and `rightDuration` must be accumulated on side switches
  - When switching sides: Calculate `elapsed = now - timestamp.seconds`, add to current side's duration
  - When completing: Accumulate final elapsed time before saving to `lastNursing`
- **Timer Display**: App shows total duration as `leftDuration + rightDuration` on home page
- **Timestamp vs timerStartTime**:
  - `timestamp.seconds`: Last update time (changes on pause/resume/switch)
  - `timerStartTime`: Absolute session start (never changes during session)
  - Both are in **seconds** (unlike sleep which uses milliseconds for timerStartTime)
```

**How to Detect Active Feeding**:
```python
feed_doc = firestore.collection("feed").document(user_uid).get()
data = feed_doc.to_dict()

if data.get("timer", {}).get("active") and not data.get("timer", {}).get("paused"):
    # Baby is actively feeding
    start_time = data["timer"]["timerStartTime"]
    left_duration = data["timer"]["leftDuration"]
    right_duration = data["timer"]["rightDuration"]
```

**Intervals Subcollection**: `feed/{child_uid}/intervals`

Feeding history is stored in the intervals subcollection with mode-specific fields:

**Breastfeeding Intervals**:
```javascript
{
  "mode": "breast",
  "start": 1763541213.0,
  "lastSide": "right",
  "lastUpdated": 1763542436.0,
  "leftDuration": 1223.0,          // Seconds
  "rightDuration": 0.0,            // Seconds
  "offset": -120.0,
  "end_offset": -120.0
}
```

**Bottle Feeding Intervals** ⚠️ **CRITICAL Field Name Inconsistency**:
```javascript
{
  "mode": "bottle",
  "start": 1768170690.723,
  "lastUpdated": 1768170723.983,
  "bottleType": "Formula",         // "Breast Milk", "Formula", or "Mixed"
  "amount": 515.0,                 // ⚠️ "amount" in intervals (NOT "bottleAmount")
  "units": "ml",                   // ⚠️ "units" in intervals (NOT "bottleUnits")
  "offset": -120.0,
  "end_offset": -120.0,
  "notes": "Optional note"         // Optional field
}
```

**CRITICAL NOTE - Field Name Inconsistency**:
- **Intervals** (`feed/{child_uid}/intervals`) use: `amount` and `units`
- **Prefs** (`prefs.lastBottle`) use: `bottleAmount` and `bottleUnits`
- Both use: `bottleType` (consistent naming)
- This inconsistency exists in the Firebase schema and must be handled in API implementations

**Document ID Format**: `{timestamp_ms}-{random_20_chars}` (e.g., `1768170723983-4158d678382fa5976c10`)

### 3. `sleep/{child_id}` - Sleep Tracking
**Purpose**: Active sleep timer and sleep history

**Structure**:
```javascript
{
  "timer": {
    "active": true/false,           // Is baby currently sleeping?
    "paused": true/false,            // Is sleep timer paused?
    "timestamp": {                   // When sleep started (server-side marker)
      "seconds": 1763548539.482
    },
    "local_timestamp": 1763548539.482, // Local time marker
    "timerStartTime": 1763548539482,   // Milliseconds since epoch
    "uuid": "<16-hex>",              // Session ID used by app
    "details": {                      // Conditions/locations structures used by app UI
      "startSleepCondition": { ... },
      "sleepLocations": { ... },
      "endSleepCondition": { ... }
    }
  },
  "prefs": {
    "lastSleep": {                   // Last completed sleep session
      "start": 1763542446.0,         // Unix timestamp (seconds)
      "duration": 6090.635,          // Duration in seconds
      "offset": -120.0               // Timezone offset
    },
    "timestamp": {
      "seconds": 1763548538.634
    },
    "local_timestamp": 1763548538.634,
    "sweetSpotWhich": 1.0            // Sleep window tracking
  }
}
```

**How to Detect Sleep State**:
```python
sleep_doc = firestore.collection("sleep").document(child_id).get()
data = sleep_doc.to_dict()

timer = data.get("timer", {})
if timer.get("active") and not timer.get("paused"):
  # Baby is currently sleeping
  start_time_sec = float(timer.get("timerStartTime", 0)) / 1000.0 if timer.get("timerStartTime") else timer.get("timestamp", {}).get("seconds", time.time())
  duration = time.time() - start_time_sec
  print(f"Baby has been sleeping for {duration:.0f} seconds")
else:
  # Baby is awake
  last_sleep = data.get("prefs", {}).get("lastSleep")
  if last_sleep:
    print(f"Last sleep was {last_sleep['duration']} seconds")
  else:
    print("No previous sleep found")
```

### 4. `childs/{child_id}` - Child Profile
**Purpose**: Child information and sleep settings

**Key Fields**:
```javascript
{
  "childsName": "Baby Name",
  "birthdate": "2025-02-22",         // Date string in YYYY-MM-DD format
  "gender": "boy/girl/other",
  "picture": "base64_or_url",
  "color": "#hexcolor",
  "createdAt": 1735776123000,
  "nightStart": "19:00",             // When nighttime starts
  "morningCutoff": "06:00",          // When morning begins
  "categories": {                    // Activity categories
    "sleep": true,
    "feed": true,
    "diaper": true
  },
  "naps": {                          // Nap schedule config
    "maxNaps": 3,
    "napLengths": [...]
  },
  "sleep_scheduler": {...},          // Sleep training settings
  "sweetspot": {...},                // Optimal sleep windows
  "analytics": {...}                 // Usage statistics
}
```

### 5. `diaper/{child_uid}` - Diaper Tracking
**Purpose**: Diaper change history and preferences

**Structure**:
```javascript
{
  "prefs": {
    "timestamp": {"seconds": 1764589219.349},
    "local_timestamp": 1764589219.349,
    "lastDiaper": {
      "start": 1764589219.349,
      "mode": "dry",  // "pee", "poo", "both", or "dry"
      "offset": -120.0
    },
    "lastPotty": {},  // Potty training (not commonly used)
    "reminderV2": {
      "atReminder": {},
      "mode": "at",
      "inReminder": {
        "value": 0.0,
        "daytimeOnly": true,
        "vibration": true,
        "enabled": false,
        "days": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        "sound": true
      }
    }
  }
}
```

**Intervals Subcollection**: `diaper/{child_uid}/intervals`

Diaper changes are logged as instant events (no timer) in the intervals subcollection:

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

**Key Implementation Notes**:
- Document ID format: `{timestamp_ms}-{random_20_chars}` (e.g., `1764589218239-b7e29bb088364a988990`)
- Unlike sleep/feeding, diapers are instant events with no active/paused state
- Color and consistency are optional and only apply to poo entries
- Quantity defaults: 50.0 for pee, 100.0 for poo

## Data Access Pattern for Home Assistant

### 1. Authentication Flow
```python
# 1. Sign in with email/password
auth_response = requests.post(
    f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
    params={"key": API_KEY},
    json={"email": email, "password": password, "returnSecureToken": True}
)
id_token = auth_response.json()["idToken"]
user_uid = auth_response.json()["localId"]

# 2. Create Firestore credentials
from google.cloud.firestore import Client
from google.auth.credentials import Credentials

class FirebaseTokenCredentials(Credentials):
    def __init__(self, id_token):
        super().__init__()
        self._id_token = id_token

    def refresh(self, request):
        pass  # Token refresh handled separately

    @property
    def token(self):
        return self._id_token

credentials = FirebaseTokenCredentials(id_token)
client = Client(project="simpleintervals", credentials=credentials)
```

### 2. Get Current Sleep State
```python
# Get user's selected child
user_doc = client.collection("users").document(user_uid).get()
child_id = user_doc.to_dict()["lastChild"]

# Check if baby is sleeping
sleep_doc = client.collection("sleep").document(child_id).get()
sleep_data = sleep_doc.to_dict()

is_sleeping = sleep_data.get("timer", {}).get("active", False)
is_paused = sleep_data.get("timer", {}).get("paused", False)

# Active sleep = timer is active AND not paused
currently_sleeping = is_sleeping and not is_paused
```

### 3. Get Sleep Duration
```python
if currently_sleeping:
    start_time = sleep_data["timer"]["timestamp"]["seconds"]
    duration_seconds = time.time() - start_time
else:
    # Last sleep from history
    last_sleep = sleep_data["prefs"]["lastSleep"]
    duration_seconds = last_sleep["duration"]
    start_time = last_sleep["start"]
```

### 4. Get Child Information
```python
child_doc = client.collection("childs").document(child_id).get()
child_data = child_doc.to_dict()

child_name = child_data["childsName"]
birth_date = child_data["birthdate"] / 1000  # Convert ms to seconds
age_days = (time.time() - birth_date) / 86400
```

## Security Rules
The Firestore database uses Security Rules that:
- ✅ Allow user-scoped document reads: `users/{uid}`, `feed/{uid}`, `sleep/{child_id}`, `childs/{child_id}`
- ❌ Block REST API access (forces Firebase SDK usage)
- ❌ Block global collection listing
- ✅ Require Bearer token authentication via Firebase SDK

## Home Assistant Integration Strategy

### Binary Sensor: `binary_sensor.baby_sleeping`
- **State**: `on` when `sleep/{child_id}/timer/active == true AND paused == false`
- **Attributes**:
  - `child_name`: From `childs/{child_id}/childsName`
  - `duration`: Current sleep duration in seconds
  - `start_time`: ISO timestamp from `timer/timestamp/seconds`
  - `age_days`: Child's age in days

### Sensor: `sensor.last_sleep_duration`
- **State**: Duration of last sleep in minutes
- **Value**: From `sleep/{child_id}/prefs/lastSleep/duration / 60`
- **Attributes**:
  - `start_time`: ISO timestamp
  - `end_time`: Calculated from start + duration

### Sensor: `sensor.baby_feeding_status`
- **State**: `feeding`, `paused`, or `idle`
- **Logic**: Check `feed/{uid}/timer/active` and `paused`
- **Attributes**:
  - `duration`: Active feeding duration
  - `side`: Left/right breast or bottle type
  - `left_duration`, `right_duration`: Time per side

## Update Strategy
- **Polling**: Query Firestore every 30-60 seconds
- **Token Refresh**: Refresh Firebase token before 1-hour expiry
- **Error Handling**: Handle network errors, token expiry, permission issues

## Example Full Query
```python
import time
from google.cloud.firestore import Client

def get_baby_status(client, user_uid):
    # Get child ID
    user_doc = client.collection("users").document(user_uid).get()
    child_id = user_doc.to_dict()["lastChild"]

    # Get child info
    child_doc = client.collection("childs").document(child_id).get()
    child_data = child_doc.to_dict()

    # Get sleep status
    sleep_doc = client.collection("sleep").document(child_id).get()
    sleep_data = sleep_doc.to_dict()

    # Get feed status
    feed_doc = client.collection("feed").document(user_uid).get()
    feed_data = feed_doc.to_dict()

    # Get diaper status
    diaper_doc = client.collection("diaper").document(child_id).get()
    diaper_data = diaper_doc.to_dict()

    # Parse sleep
    is_sleeping = (
        sleep_data.get("timer", {}).get("active", False) and
        not sleep_data.get("timer", {}).get("paused", False)
    )

    # Parse feeding
    is_feeding = (
        feed_data.get("timer", {}).get("active", False) and
        not feed_data.get("timer", {}).get("paused", False)
    )

    # Parse last diaper
    last_diaper = diaper_data.get("prefs", {}).get("lastDiaper", {})

    return {
        "child_name": child_data["childsName"],
        "is_sleeping": is_sleeping,
        "is_feeding": is_feeding,
        "sleep_start": sleep_data.get("timer", {}).get("timestamp", {}).get("seconds") if is_sleeping else None,
        "feed_start": feed_data.get("timer", {}).get("timerStartTime") if is_feeding else None,
        "last_sleep_duration": sleep_data["prefs"]["lastSleep"]["duration"] / 60,  # minutes
        "last_diaper_mode": last_diaper.get("mode"),
        "last_diaper_time": last_diaper.get("start"),
    }
```

### 6. `insights/{child_uid}` - AI Insights & Recommendations
**Purpose**: Huckleberry's AI-powered insights, daily tips, and mini-plans

**Subcollections**:
- `insights/{child_uid}/dailyTips` - Daily parenting tips documents
- `insights/{child_uid}/miniPlans` - Mini-plans documents

**Structure** (per document in subcollections):
```javascript
{
  "id": "tip_id_123",
  "content": "Tip content...",
  "category": "sleep|feeding|development",
  "viewed": true/false,
  "lastUpdated": 1234567890,
  "feedback": {
    "thumbs": true/false,           // thumbsUp or thumbsDown
    "result": {...},
    "response": {...}
  }
}
```

**Notes**:
- Each child has their own insights collection
- Feedback is stored per insight document
- Used for personalized recommendations based on tracking data

### 7. `types/{child_uid}` - Custom Activity Types
**Purpose**: User-created custom activity types beyond defaults

**Main Document** (`types/{child_uid}`):
```javascript
{
  "lastUpdated": 1234567890,
  // Predefined type preferences
}
```

**Subcollection** (`types/{child_uid}/custom`):
```javascript
{
  "name": "Tummy Time",
  "icon": "play",
  "color": "#FF5733",
  "category": "activity",
  "created": 1234567890,
  "image": ""  // Optional custom image URL
}
```

**Notes**:
- Allows users to track custom activities not in default list
- Each custom type has its own document
- Can have custom icons or images

### 8. `notifications/{uid}` - User Notifications
**Purpose**: Push notifications and in-app notification history

**Main Document** (`notifications/{uid}`):
```javascript
{
  "lastUpdated": 1234567890,
  // Notification preferences
}
```

**Subcollection** (`notifications/{uid}/messages`):
```javascript
{
  "type": "insight|reminder|celebration",
  "title": "Notification title",
  "message": "Notification message",
  "read": true/false,
  "timestamp": 1234567890,
  "data": {
    // Type-specific data
  }
}
```

**Notes**:
- Stored per user (not per child)
- Messages stored in `messages` subcollection
- Can be deleted via `deleteDocumentFromFirestoreCollection`
- Used for in-app notification center
- Found in latest decompiled output: `"notifications/" + this["uid"] + "/messages"`

### 9. `recommendations` - Recommendations System
**Purpose**: Sleep plan recommendations, questionnaires, and personalized recommendation data

**Global Collection** (`recommendations`):
```javascript
{
  "type": "feature|tip|update",
  "content": "Recommendation content...",
  "priority": "high|medium|low",
  "targetAudience": "all|premium|trial",
  "created": 1234567890,
  "expires": 1234567890
}
```

**Per-Child Document** (`recommendations/{child_uid}`):
```javascript
{
  "lastUpdated": 1234567890,
  "answers": {
    // Questionnaire answers for recommendation engine
    "sleepEnvironment": "...",
    "bedtimeRoutine": "...",
    // etc.
  }
}
```

**Subcollection** (`recommendations/{child_uid}/recset`):
```javascript
{
  "id": "rec_123",
  "type": "sleep_plan|tip|action",
  "title": "Recommendation title",
  "content": "Detailed recommendation content",
  "priority": 1,
  "status": "active|completed|dismissed",
  "created": 1234567890,
  "viewed": true/false
}
```

**Notes**:
- Works as both global collection AND per-child structure
- `recommendations` (root) = system-wide recommendations
- `recommendations/{child_uid}` (document) = stores questionnaire answers in `recDb`
- `recommendations/{child_uid}/recset` (subcollection) = personalized recommendation set
- Listened to via `recDbListeners` and `recsListeners`
- Found in decompiled output: line 40408 (global), line 10373 (per-child)

### 10. `health/{child_uid}` - Health/Medication Tracking
**Purpose**: Medication administration and health events tracking

**Main Document** (`health/{child_uid}`):
```javascript
{
  "prefs": {
    "lastMedication": {
      "start": 1234567890.0,
      "medication_name": "Tylenol",
      "medication_type": "pain_relief",
      "dosage": "5ml",
      "notes": "",
      "offset": -120.0
    },
    "lastTemperature": {
      "start": 1234567890.0,
      "temperature": 98.6,
      "units": "fahrenheit",
      "offset": -120.0
    },
    "lastGrowth": {
      "start": 1234567890.0,
      "weight": 15.5,
      "height": 24.0,
      "head": 18.0,
      "units": "imperial",
      "offset": -120.0
    },
    "reminderV2": {
      // Medication reminder config
    }
  }
}
```

**Subcollection** (`health/{child_uid}/data`) - **CRITICAL: Uses "data", not "intervals"!**:
```javascript
// Growth entry example
{
  "_id": "1733068116606-a04ff18de85c4a98a451",
  "type": "health",
  "mode": "growth",
  "start": 1733068116.606,
  "lastUpdated": 1733068116.606,
  "offset": -120.0,
  "isNight": false,
  "multientry_key": null,
  "weight": 5.2,
  "weightUnits": "kg",
  "height": 65.5,
  "heightUnits": "cm",
  "head": 42.3,
  "headUnits": "hcm"
}

// Other health modes: "temperature", "medication"
{
  "start": 1234567890.0,
  "lastUpdated": 1234567890.0,
  "mode": "medication",
  "medication_name": "Tylenol",
  "dosage": "5ml",
  "notes": "",
  "offset": -120.0
}
```

**Note**: Health tracker is unique - all other trackers (sleep, feed, diaper, pump, activities, solids) use `intervals` subcollection, but health uses `data` subcollection.

**Subcollection** (`health/{child_uid}/types`):
```javascript
{
  "name": "Custom Medicine Name",
  "type": "custom_medication",
  "created": 1234567890,
  "icon": "medicine"
}
```

**Notes**:
- Groups medication, temperature, and growth tracking
- Custom medication types stored in `types` subcollection
- Functions: `addHealthData`, `deleteHealthData`, `getHealthPrefs`, `setHealthPrefs`
- Found in latest decompiled output: `"health/" + child_uid + "/types"` (line 110482)
- Follows same pattern as other activity collections

### 11. Additional Activity Collections (Verified in Latest Output)
Based on code references from both decompiled outputs, these collections follow the same pattern as `sleep`, `feed`, and `diaper`:

- **`pump/{child_uid}`** - Pumping/expressing milk tracking
  - Subcollection: `pump/{child_uid}/intervals`
  - Referenced extensively in latest output (lines 1265, 1682, 2428, etc.)

- **`potty/{child_uid}`** - Potty training tracking
  - Subcollection: `potty/{child_uid}/intervals`
  - Part of diaper tracking (toggle: `isPotty` field)
  - Referenced in multiple files (2823, 4364, 5204, etc.)

- **`solids/{child_uid}`** - Solid food introduction tracking
  - Subcollection: `solids/{child_uid}/intervals`
  - Extensively referenced (lines 1682, 2486, 2608, etc.)

- **`activities/{child_uid}`** - General activities (tummy time, play, etc.)
  - Subcollection: `activities/{child_uid}/intervals`
  - Referenced in calendar/daily view code

**Status**: All four collections confirmed in latest decompiled output (December 2025). They use the same timer/intervals pattern as verified collections but have not yet been tested with live Firestore access.

### 12. `feedback/{child_uid}/feedback` - User Feedback on Insights
**Purpose**: Store user feedback on recommendations, insights, and AI suggestions

**Subcollection Structure** (`feedback/{child_uid}/feedback`):
```javascript
{
  "id": "feedback_123",
  "type": "insight|recommendation|tip",
  "targetId": "insight_id_or_rec_id",
  "rating": "positive|negative",
  "thumbs": true/false,  // thumbsUp or thumbsDown
  "comment": "Optional user comment",
  "timestamp": 1234567890,
  "resolved": true/false
}
```

**Notes**:
- Stores per-child feedback on various features
- Used to improve AI recommendations
- Listened to via `feedbackListeners`
- Found in decompiled output at line 10402: `feedback/${c}/feedback`
- Collection is monitored for real-time changes

## Complete Collection Hierarchy

### Root Collections (14):
1. `users/{uid}`
2. `childs/{child_id}`
3. `sleep/{child_uid}`
4. `feed/{child_uid}`
5. `diaper/{child_uid}`
6. `potty/{child_uid}` (likely)
7. `pump/{child_uid}`
8. `solids/{child_uid}`
9. `health/{child_uid}`
10. `activities/{child_uid}` (likely)
11. `insights/{child_uid}`
12. `types/{child_uid}`
13. `notifications/{uid}`
14. `recommendations` (global + per-child)

### Subcollections (13):
1. `sleep/{child_uid}/intervals`
2. `feed/{child_uid}/intervals`
3. `diaper/{child_uid}/intervals`
4. `potty/{child_uid}/intervals` (likely)
5. `pump/{child_uid}/intervals`
6. `solids/{child_uid}/intervals`
7. **`health/{child_uid}/data`** ⚠️ Uses "data", NOT "intervals"!
8. `activities/{child_uid}/intervals` (likely)
9. `health/{child_uid}/types` - Custom medication types
10. `types/{child_uid}/custom` - Custom activity types
11. `insights/{child_uid}/dailyTips` (likely)
12. `insights/{child_uid}/miniPlans` (likely)
13. `notifications/{uid}/messages`
14. **`recommendations/{child_uid}/recset`** - Personalized recommendations
15. **`feedback/{child_uid}/feedback`** - User feedback

**Total: 14 root collections + 15 subcollections = 29 collection paths**

**CRITICAL NOTE**: Health tracker uniquely uses `data` subcollection instead of `intervals`. This was discovered through decompiled JavaScript (module 38473). All other trackers use `intervals`.

## Notes
- All timestamps are Unix timestamps (seconds since epoch)
- Some timestamps use `{"seconds": xxx}` format (Firestore Timestamp)
- Timezone offsets are in minutes (negative = UTC offset)
- Duration fields are in seconds (divide by 60 for minutes)
- The `timer` object only exists when there's an active or paused session
- Child ID is often the same as user UID for single-child accounts
- Collections 6-12 discovered through decompiled source analysis (December 2025)
- **Validation**: Collections 1-5 verified via live Firestore testing
- **Validation**: Collections 6-12 confirmed in decompiled sources (v0.9.258 and latest)
- **New Collections Found**:
  - `recommendations` (both global and per-child with `/recset` subcollection)
  - `feedback/{child_uid}/feedback` (user feedback subcollection)
  - `notifications/{uid}/messages` (notification messages subcollection)
  - `health/{child_uid}` (health/medication tracking with `/types` and `/intervals`)
- **Listener Names** (from code):
  - `timerListeners` - Activity timers (sleep, feed, diaper, pump, health, activities)
  - `trackerIntervalListeners` - Interval history for all activities
  - `childsListeners` - Child document changes
  - `recsListeners` - Personalized recommendations (`recommendations/{child_uid}/recset`)
  - `recDbListeners` - Recommendation answers (`recommendations/{child_uid}`)
  - `feedbackListeners` - User feedback (`feedback/{child_uid}/feedback`)
  - `notificationsListener` - Notification messages
