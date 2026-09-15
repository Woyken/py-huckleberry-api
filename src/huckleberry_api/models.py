"""API input models.

Return models are defined in `firebase_types.py` to keep API outputs Firebase-native.
"""

from __future__ import annotations

from .firebase_types import (
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
