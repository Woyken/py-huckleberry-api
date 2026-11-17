# Integration Tests

This directory contains integration tests for the Huckleberry API library.

## Requirements

Integration tests require valid Huckleberry account credentials set as environment variables:

- `HUCKLEBERRY_EMAIL`: Your Huckleberry account email
- `HUCKLEBERRY_PASSWORD`: Your Huckleberry account password
- `HUCKLEBERRY_TIMEZONE`: IANA timezone (for example `UTC` or `Europe/London`)

## Running Tests Locally

1. Install development dependencies:
```bash
uv sync --dev
```

2. Set environment variables:
```bash
# PowerShell
$env:HUCKLEBERRY_EMAIL = "your-email@example.com"
$env:HUCKLEBERRY_PASSWORD = "your-password"
$env:HUCKLEBERRY_TIMEZONE = "UTC"

# Bash/Linux
export HUCKLEBERRY_EMAIL="your-email@example.com"
export HUCKLEBERRY_PASSWORD="your-password"
export HUCKLEBERRY_TIMEZONE="UTC"
```

3. Run tests:
```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test module
uv run pytest tests/test_sleep.py -v

# Run specific test class
uv run pytest tests/test_authentication.py::TestAuthentication -v

# Run specific test
uv run pytest tests/test_sleep.py::TestSleepTracking::test_start_and_cancel_sleep -v

# Validate latest live Firebase entries against strict schemas
uv run pytest tests/test_live_firebase_models.py -q
```

## Test Coverage

The integration tests are organized into separate modules:

### `test_authentication.py`
- **Authentication**: Login, token refresh, invalid credentials
- **Children Retrieval**: Getting child profiles and data
- **Error Handling**: Invalid credentials, missing authentication

### `test_sleep.py`
- **Sleep Tracking**: Start, pause, resume, cancel, complete sleep cycles
- **Data Consistency**: Verifying sleep intervals are created correctly

### `test_feeding.py`
- **Feeding Tracking**: Start, pause, resume, side switching, complete feeding
- **Data Consistency**: Verifying feeding intervals are created correctly

### `test_diaper.py`
- **Diaper Tracking**: Logging pee, poo, both, and dry checks

### `test_growth.py`
- **Growth Tracking**: Logging measurements in metric and imperial units
- **Data Retrieval**: Getting growth history

### `test_listeners.py`
- **Real-time Listeners**: Sleep, feeding, diaper, and health listeners with token refresh

### `test_live_firebase_models.py`
- **Live Schema Validation**: Validates latest live Firebase payloads against strict models in `src/huckleberry_api/firebase_types.py`
- Run app actions first, then run this test to validate newest entries
- Optional env var: `HUCKLEBERRY_MODEL_VALIDATION_MAX_DOCS` (default `20`)

### `test_entries.py`
- **Offline**: entry id parsing, change validation, `prefs.last*` summary projection, edit and delete against an in-memory Firestore
- **Live**: log, list with ids, update, delete, and summary restore for each tracker. These tests delete the entries they create

## CI/CD

Integration tests run automatically on:
- Pushes to `main` branch
- Pull requests to `main` branch
- Manual workflow dispatch

GitHub Actions workflow: `.github/workflows/integration-tests.yml`

Tests run on Python 3.14.

## Important Notes

⚠️ **WARNING**: These tests perform real operations on your Huckleberry account:
- They will create and cancel sleep/feeding timers
- They will log diaper changes and growth measurements
- They will create intervals in your history

**Recommendation**: Use a test account with test child data, not your real baby tracking account.

## Skipped Tests

Tests will automatically skip if:
- Environment variables are not set
- No children exist in the account
- Authentication fails

## Test Duration

Full test suite takes approximately 2-3 minutes to run due to:
- Real Firebase operations with network latency
- Intentional delays (`await asyncio.sleep()`) to allow Firebase propagation
- Multiple test scenarios with setup/teardown
