"""Public exceptions raised by history record mutations."""

from __future__ import annotations


class HuckleberryRecordError(RuntimeError):
    """Base class for safe history record mutation failures."""


class HuckleberryRecordNotFoundError(HuckleberryRecordError):
    """The referenced history record no longer exists."""


class HuckleberryRecordConflictError(HuckleberryRecordError):
    """The referenced history record changed after it was read."""


class HuckleberryRecordReferenceError(HuckleberryRecordError):
    """The supplied history record reference is malformed or mismatched."""
