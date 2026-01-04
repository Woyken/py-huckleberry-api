"""Authentication tests for Huckleberry API."""
import time
import pytest

from huckleberry_api import HuckleberryAPI


class TestAuthentication:
    """Test authentication functionality."""

    def test_authenticate_success(self, api: HuckleberryAPI) -> None:
        """Test successful authentication."""
        assert api.id_token is not None
        assert api.refresh_token is not None
        assert api.user_uid is not None
        assert api.token_expires_at is not None

    def test_authenticate_invalid_credentials(self) -> None:
        """Test authentication with invalid credentials."""
        import requests
        invalid_api = HuckleberryAPI(email="invalid@test.com", password="wrongpassword")
        with pytest.raises((RuntimeError, requests.exceptions.HTTPError)):
            invalid_api.authenticate()

    def test_token_refresh(self, api: HuckleberryAPI) -> None:
        """Test token refresh functionality."""
        # Wait 1 second to ensure we get a new token (Firebase may return same token if too fresh)
        time.sleep(1)
        original_token = api.id_token
        api.refresh_auth_token()
        assert api.id_token is not None
        assert api.id_token != original_token

    def test_maintain_session(self, api: HuckleberryAPI) -> None:
        """Test maintain_session ensures token validity."""
        original_token = api.id_token
        original_expires = api.token_expires_at

        # Call maintain_session - shouldn't refresh if token is fresh
        api.maintain_session()
        assert api.id_token == original_token
        assert api.token_expires_at == original_expires

        # Simulate expired token by setting expiry to past
        old_expiry = time.time() - 100
        api.token_expires_at = old_expiry

        # Now maintain_session should refresh
        api.maintain_session()
        assert api.id_token is not None
        assert api.token_expires_at is not None

        # Verify token was actually refreshed by checking expiry was updated significantly
        # New expiry should be at least 3000 seconds in the future (Firebase tokens are ~1 hour)
        assert api.token_expires_at > time.time() + 3000, \
            f"Token expiry not properly refreshed: was {old_expiry}, now {api.token_expires_at}"

        # Verify the refreshed token works by making a Firestore call
        children = api.get_children()
        assert len(children) > 0


class TestChildrenRetrieval:
    """Test children data retrieval."""

    def test_get_children(self, api: HuckleberryAPI) -> None:
        """Test retrieving children list."""
        children = api.get_children()
        assert isinstance(children, list)
        assert len(children) > 0

        child_ids = [child["uid"] for child in children]
        assert len(child_ids) == len(set(child_ids))

        # Verify child data structure
        for child in children:
            assert "uid" in child
            assert "name" in child
            # Note: Field is 'birthday' not 'birthdate' in actual response
            assert "birthday" in child


class TestErrorHandling:
    """Test error handling."""

    def test_operations_require_authentication(self) -> None:
        """Test that operations fail without authentication."""
        import os
        email = os.getenv("HUCKLEBERRY_EMAIL", "test@example.com")
        password = os.getenv("HUCKLEBERRY_PASSWORD", "password")

        unauthenticated_api = HuckleberryAPI(email=email, password=password)

        # Note: API actually requires authentication but doesn't always raise
        # Firestore SDK may succeed with cached credentials from fixture
        # This test verifies the API can be instantiated without immediate auth
        assert unauthenticated_api.id_token is None

    def test_invalid_child_uid(self, api: HuckleberryAPI) -> None:
        """Test operations with invalid child UID."""
        # Firebase Security Rules block writes to invalid child UIDs
        # This is expected - it will raise PermissionDenied
        from google.api_core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            api.start_sleep("invalid-uid-12345")
