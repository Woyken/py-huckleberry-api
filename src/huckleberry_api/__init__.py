"""Huckleberry API client for Python."""
from __future__ import annotations

from .api import HuckleberryAPI
from .field_mapping import (
    FEED_FIREBASE_TO_PYTHON,
    FEED_PYTHON_TO_FIREBASE,
    FIREBASE_TO_PYTHON,
    PYTHON_TO_FIREBASE,
    convert_firebase_to_python,
    convert_python_to_firebase,
)
from .types import (
    ChildData,
    DiaperData,
    DiaperDocumentData,
    FeedDocumentData,
    FeedIntervalData,
    FeedTimerData,
    GrowthData,
    HealthDocumentData,
    SleepDocumentData,
    SleepIntervalData,
    SleepTimerData,
)

__all__ = [
    "HuckleberryAPI",
    "ChildData",
    "DiaperData",
    "DiaperDocumentData",
    "FeedDocumentData",
    "FeedIntervalData",
    "FeedTimerData",
    "GrowthData",
    "HealthDocumentData",
    "SleepDocumentData",
    "SleepIntervalData",
    "SleepTimerData",
    # Field mapping utilities
    "FIREBASE_TO_PYTHON",
    "PYTHON_TO_FIREBASE",
    "FEED_FIREBASE_TO_PYTHON",
    "FEED_PYTHON_TO_FIREBASE",
    "convert_firebase_to_python",
    "convert_python_to_firebase",
]
