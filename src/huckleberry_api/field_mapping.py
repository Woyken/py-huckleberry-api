"""Field name mappings between Firebase (camelCase) and Python API (snake_case with units)."""

from typing import Any

# Firebase field name → Python API field name
FIREBASE_TO_PYTHON = {
    # Child fields
    "birthdate": "birthday",
    "createdAt": "created_at",
    "nightStart": "night_start_min",
    "morningCutoff": "morning_cutoff_min",
    "expectedNaps": "expected_naps",

    # Timer fields (common)
    "local_timestamp": "local_timestamp_sec",

    # Sleep timer fields
    "timerStartTime": "timer_start_time_ms",  # Sleep uses milliseconds

    # Feed timer fields
    "feedStartTime": "feed_start_time_sec",
    "leftDuration": "left_duration_sec",
    "rightDuration": "right_duration_sec",
    "lastSide": "last_side",
    "activeSide": "active_side",

    # Interval fields
    "lastUpdated": "last_updated_sec",
    "start": "start_sec",
    "duration": "duration_sec",
    "offset": "offset_min",
    "end_offset": "end_offset_min",
}

# Python API field name → Firebase field name
PYTHON_TO_FIREBASE = {v: k for k, v in FIREBASE_TO_PYTHON.items()}

# Special handling for feed timer (timerStartTime is in seconds for feeding)
FEED_FIREBASE_TO_PYTHON = {
    **FIREBASE_TO_PYTHON,
    "timerStartTime": "timer_start_time_sec",  # Feed uses seconds (override)
}

FEED_PYTHON_TO_FIREBASE = {v: k for k, v in FEED_FIREBASE_TO_PYTHON.items()}


def convert_firebase_to_python(data: dict[str, Any], mapping: dict[str, str] = FIREBASE_TO_PYTHON) -> dict[str, Any]:
    """Convert Firebase field names to Python API field names.

    Args:
        data: Dictionary with Firebase field names (camelCase)
        mapping: Field name mapping to use (default: FIREBASE_TO_PYTHON)

    Returns:
        Dictionary with Python API field names (snake_case with units)
    """
    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        # Convert nested dictionaries recursively
        if isinstance(value, dict):
            value = convert_firebase_to_python(value, mapping)
        elif isinstance(value, list):
            value = [convert_firebase_to_python(item, mapping) if isinstance(item, dict) else item
                    for item in value]

        # Map field name or keep original
        python_key = mapping.get(key, key)
        result[python_key] = value

    return result


def convert_python_to_firebase(data: dict[str, Any], mapping: dict[str, str] = PYTHON_TO_FIREBASE) -> dict[str, Any]:
    """Convert Python API field names to Firebase field names.

    Args:
        data: Dictionary with Python API field names (snake_case with units)
        mapping: Field name mapping to use (default: PYTHON_TO_FIREBASE)

    Returns:
        Dictionary with Firebase field names (camelCase)
    """
    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        # Convert nested dictionaries recursively
        if isinstance(value, dict):
            value = convert_python_to_firebase(value, mapping)
        elif isinstance(value, list):
            value = [convert_python_to_firebase(item, mapping) if isinstance(item, dict) else item
                    for item in value]

        # Map field name or keep original
        firebase_key = mapping.get(key, key)
        result[firebase_key] = value

    return result
