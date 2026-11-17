"""API input and wrapper models.

Return models are defined in `firebase_types.py` to keep API outputs Firebase-native.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from .firebase_types import (
    FirebaseActivityIntervalData,
    FirebaseDiaperData,
    FirebaseFeedIntervalData,
    FirebasePumpIntervalData,
    FirebaseSleepIntervalData,
    HealthDataEntry,
    Number,
    SolidsFoodSource,
    StrictModel,
)

__all__ = ["HistoryEntry", "SolidsFoodReference", "Tracker"]

# Firestore collection names of the per-child tracker documents.
Tracker = Literal["sleep", "feed", "diaper", "health", "pump", "activities"]

HistoryRow: TypeAlias = (
    FirebaseSleepIntervalData
    | FirebaseFeedIntervalData
    | FirebaseDiaperData
    | HealthDataEntry
    | FirebasePumpIntervalData
    | FirebaseActivityIntervalData
)


class SolidsFoodReference(StrictModel):
    """Reference to an existing curated/custom food."""

    id: str
    source: SolidsFoodSource
    name: str
    amount: str | Number


class HistoryEntry(StrictModel):
    """One saved history row with the id needed to edit or delete it.

    `id` is the Firestore document id, or `<container_doc_id>/<key>` for a row
    stored inside a batched `multi` container document.
    """

    id: str
    tracker: Tracker
    data: HistoryRow
