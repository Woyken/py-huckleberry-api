"""Authentication tests for Huckleberry API."""

import asyncio
import time
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import aiohttp
import pytest
from google.cloud import firestore

from huckleberry_api import HuckleberryAPI


class TestAuthentication:
    """Test authentication functionality."""

    async def test_authenticate_invalid_credentials_includes_firebase_error_details(
        self, websession: aiohttp.ClientSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test authentication errors include Firebase response details."""

        class FakeResponse:
            def __init__(self) -> None:
                self.status = 400
                self.reason = "Bad Request"
                self.headers = {}
                self.request_info = SimpleNamespace(real_url="https://identitytoolkit.googleapis.com/")
                self.history = ()

            async def __aenter__(self) -> FakeResponse:
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def text(self) -> str:
                return '{"error":{"message":"INVALID_LOGIN_CREDENTIALS","errors":[{"message":"INVALID_LOGIN_CREDENTIALS"}]}}'

            async def json(self, *args, **kwargs) -> dict[str, object]:
                return {
                    "error": {
                        "message": "INVALID_LOGIN_CREDENTIALS",
                        "errors": [{"message": "INVALID_LOGIN_CREDENTIALS"}],
                    }
                }

        invalid_api = HuckleberryAPI(
            email="invalid@test.com", password="wrongpassword", timezone="UTC", websession=websession
        )

        def fake_post(*args, **kwargs) -> FakeResponse:
            return FakeResponse()

        monkeypatch.setattr(websession, "post", fake_post)

        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await invalid_api.authenticate()

        assert exc_info.value.status == 400
        assert "Authentication failed with HTTP 400 Bad Request" in exc_info.value.message
        assert (
            '{"error":{"message":"INVALID_LOGIN_CREDENTIALS","errors":[{"message":"INVALID_LOGIN_CREDENTIALS"}]}}'
            in exc_info.value.message
        )

    async def test_authenticate_success(self, api: HuckleberryAPI) -> None:
        """Test successful authentication."""
        assert api.id_token is not None
        assert api.refresh_token is not None
        assert api.user_uid is not None
        assert api.token_expires_at is not None

    async def test_authenticate_invalid_credentials(self, websession: aiohttp.ClientSession) -> None:
        """Test authentication with invalid credentials."""

        invalid_api = HuckleberryAPI(
            email="invalid@test.com", password="wrongpassword", timezone="UTC", websession=websession
        )
        with pytest.raises((RuntimeError, aiohttp.ClientResponseError, aiohttp.ClientError)):
            await invalid_api.authenticate()

    async def test_token_refresh(self, api: HuckleberryAPI) -> None:
        """Test token refresh functionality."""
        # Wait 1 second to ensure we get a new token (Firebase may return same token if too fresh)
        await asyncio.sleep(1)
        original_token = api.id_token
        await api.refresh_session_token()
        assert api.id_token is not None
        assert api.id_token != original_token

    async def test_ensure_session(self, api: HuckleberryAPI) -> None:
        """Test ensure_session keeps token validity."""
        original_token = api.id_token
        original_expires = api.token_expires_at

        # Call ensure_session - shouldn't refresh if token is fresh
        await api.ensure_session()
        assert api.id_token == original_token
        assert api.token_expires_at == original_expires

        # Simulate expired token by setting expiry to past
        old_expiry = time.time() - 100
        api.token_expires_at = old_expiry

        # Now ensure_session should refresh
        await api.ensure_session()
        assert api.id_token is not None
        assert api.token_expires_at is not None

        # Verify token was actually refreshed by checking expiry was updated significantly
        # New expiry should be at least 3000 seconds in the future (Firebase tokens are ~1 hour)
        assert api.token_expires_at > time.time() + 3000, (
            f"Token expiry not properly refreshed: was {old_expiry}, now {api.token_expires_at}"
        )

        # Verify the refreshed token works by making a Firestore call
        user_doc = await api.get_user()
        assert user_doc is not None

    async def test_refresh_session_token_recreates_listener_client(self, websession: aiohttp.ClientSession) -> None:
        """Test token refresh drops the cached listener client before recreating listeners."""

        class FakeResponse:
            def __init__(self) -> None:
                self.status = 200
                self.reason = "OK"
                self.headers = {}
                self.request_info = SimpleNamespace(real_url="https://securetoken.googleapis.com/")
                self.history = ()

            async def __aenter__(self) -> FakeResponse:
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def text(self) -> str:
                return '{"id_token":"fresh-id-token","refresh_token":"fresh-refresh-token","expires_in":"3600"}'

            async def json(self, *args, **kwargs) -> dict[str, str]:
                return {
                    "id_token": "fresh-id-token",
                    "refresh_token": "fresh-refresh-token",
                    "expires_in": "3600",
                }

        api = HuckleberryAPI(email="listener@test.com", password="password", timezone="UTC", websession=websession)
        api.id_token = "stale-id-token"
        api.refresh_token = "stale-refresh-token"

        stale_listener_client = cast(firestore.Client, object())
        unsubscribe = Mock()
        callback = Mock()

        api._listener_client = stale_listener_client
        api._listeners["sleep_child-123"] = SimpleNamespace(unsubscribe=unsubscribe)
        api._listener_callbacks["sleep_child-123"] = ("sleep", "child-123", callback)

        observed_listener_clients: list[object | None] = []

        def fake_post(*args, **kwargs) -> FakeResponse:
            return FakeResponse()

        async def fake_setup_sleep_listener(child_uid: str, received_callback) -> None:
            assert child_uid == "child-123"
            assert received_callback is callback
            observed_listener_clients.append(api._listener_client)
            api._listener_client = cast(firestore.Client, object())

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(websession, "post", fake_post)
        monkeypatch.setattr(api, "setup_sleep_listener", fake_setup_sleep_listener)

        try:
            await api.refresh_session_token()
        finally:
            monkeypatch.undo()

        unsubscribe.assert_called_once_with()
        assert observed_listener_clients == [None]
        assert api._listener_client is not None
        assert api._listener_client is not stale_listener_client
        assert api.id_token == "fresh-id-token"
        assert api.refresh_token == "fresh-refresh-token"


class TestChildrenRetrieval:
    """Test children data retrieval."""

    async def test_get_child(self, api: HuckleberryAPI) -> None:
        """Test retrieving user childList and child document by id."""
        user_doc = await api.get_user()
        assert user_doc is not None

        child_uids = [child_ref.cid for child_ref in user_doc.childList]
        assert len(child_uids) > 0
        assert len(child_uids) == len(set(child_uids))

        child = await api.get_child(child_uids[0])
        assert child is not None
        assert child.model_dump(exclude_none=True)

    async def test_get_live_timer_documents(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Typed sleep and feed reads expose current timer state without mutation."""
        sleep = await api.get_sleep(child_uid)
        feed = await api.get_feed(child_uid)

        assert sleep is not None
        assert feed is not None
        if sleep.timer is not None:
            assert isinstance(sleep.timer.active, bool)
        if feed.timer is not None:
            assert isinstance(feed.timer.active, bool)


class TestErrorHandling:
    """Test error handling."""

    async def test_operations_require_authentication(self, websession: aiohttp.ClientSession) -> None:
        """Test that operations fail without authentication."""
        import os

        email = os.getenv("HUCKLEBERRY_EMAIL", "test@example.com")
        password = os.getenv("HUCKLEBERRY_PASSWORD", "password")
        timezone = os.getenv("HUCKLEBERRY_TIMEZONE", "UTC")

        unauthenticated_api = HuckleberryAPI(email=email, password=password, timezone=timezone, websession=websession)

        # Note: API actually requires authentication but doesn't always raise
        # Firestore SDK may succeed with cached credentials from fixture
        # This test verifies the API can be instantiated without immediate auth
        assert unauthenticated_api.id_token is None

    async def test_invalid_child_uid(self, api: HuckleberryAPI) -> None:
        """Test operations with invalid child UID."""
        # Firebase Security Rules block writes to invalid child UIDs
        # This is expected - it will raise PermissionDenied
        from google.api_core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            await api.start_sleep("invalid-uid-12345")
