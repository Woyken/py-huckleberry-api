"""Shared internals for the Huckleberry API package."""

from __future__ import annotations

import logging

# Pinned to the historical module name so logger configuration keeps working
# after the api.py split into feature modules.
_LOGGER = logging.getLogger("huckleberry_api.api")
