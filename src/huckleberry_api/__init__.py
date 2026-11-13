"""Huckleberry API client for Python."""

from __future__ import annotations

from .api import HuckleberryAPI

__all__ = ["HuckleberryAPI"]

del annotations  # bound by the future import; keep the public namespace clean
