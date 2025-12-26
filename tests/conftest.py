"""Shared fixtures for integration tests.

These fixtures are automatically discovered by pytest and available to all test files.
"""
import os

import pytest

from huckleberry_api import HuckleberryAPI


@pytest.fixture(scope="module")
def api() -> HuckleberryAPI:
    """Create API instance with credentials from environment."""
    email = os.getenv("HUCKLEBERRY_EMAIL")
    password = os.getenv("HUCKLEBERRY_PASSWORD")
    timezone = os.getenv("HUCKLEBERRY_TIMEZONE")

    if not email or not password or not timezone:
        pytest.skip("HUCKLEBERRY_EMAIL, HUCKLEBERRY_PASSWORD, and HUCKLEBERRY_TIMEZONE environment variables required")

    api_instance = HuckleberryAPI(email=email, password=password, timezone=timezone)
    api_instance.authenticate()

    yield api_instance

    # Cleanup: stop all listeners
    api_instance.stop_all_listeners()


@pytest.fixture(scope="module")
def child_uid(api: HuckleberryAPI) -> str:
    """Get child UID for testing."""
    children = api.get_children()
    if not children:
        pytest.skip("No children found in test account")
    return children[0]["uid"]
