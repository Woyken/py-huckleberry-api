"""API-facing models.

Firebase payload models remain in `firebase_types.py`.
"""

from __future__ import annotations

from pydantic import ConfigDict

from .firebase_types import (
    FirebaseMilestoneData,
    MilestoneCategory,
    MilestoneSource,
    Number,
    SolidsFoodSource,
    StrictModel,
)


class SolidsFoodReference(StrictModel):
    """Reference to an existing curated/custom food."""

    id: str
    source: SolidsFoodSource
    name: str
    amount: str | Number


class PredefinedMilestone(StrictModel):
    """Immutable predefined milestone selection from the APK catalog."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        populate_by_name=True,
        protected_namespaces=(),
    )

    id: str
    age_months: int
    category: MilestoneCategory
    title: str
    typical_window: str
    source: MilestoneSource


class MilestoneRecord(StrictModel):
    """Milestone data paired with its Firestore document ID."""

    id: str
    milestone: FirebaseMilestoneData
