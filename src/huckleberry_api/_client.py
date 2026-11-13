"""Shared client plumbing: authentication, Firestore clients, and timezone."""

from __future__ import annotations

import aiohttp
import asyncio
import json
from datetime import datetime
from google.auth.credentials import Credentials
from google.cloud import firestore
from google.cloud.firestore_v1 import AsyncClient
from zoneinfo import ZoneInfo
from ._common import _LOGGER
from ._const import AUTH_URL
from ._const import FIREBASE_API_KEY
from ._const import REFRESH_URL


async def _raise_for_status_with_details(response: aiohttp.ClientResponse, operation: str) -> None:
    """Raise an aiohttp status error with the raw response payload included."""
    if response.status < 400:
        return

    message = f"{operation} failed with HTTP {response.status}"
    if response.reason:
        message = f"{message} {response.reason}"

    try:
        response_payload = await response.json(content_type=None)
        message = f"{message}: {json.dumps(response_payload, separators=(',', ':'))}"
    except aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeDecodeError, ValueError:
        response_body = await response.text()
        if response_body:
            message = f"{message}: {response_body}"

    raise aiohttp.ClientResponseError(
        response.request_info,
        response.history,
        status=response.status,
        message=message,
        headers=response.headers,
    )


class FirebaseTokenCredentials(Credentials):
    """Custom credentials class for Firebase SDK."""

    def __init__(self, id_token: str):
        """Initialize with Firebase ID token."""
        super().__init__()
        self._id_token = id_token
        self.token = id_token  # Set the token attribute that parent expects

    def refresh(self, request):
        """Token refresh is handled by HuckleberryAPI.

        This method is required by the Credentials interface but is not used.
        Token refreshing is managed externally by HuckleberryAPI.refresh_session_token(),
        and a new FirebaseTokenCredentials instance is created with the refreshed token.
        """


class HuckleberryBase:
    """Shared authentication, Firestore client, and timezone plumbing."""

    def __init__(self, email: str, password: str, timezone: str, websession: aiohttp.ClientSession) -> None:
        """Initialize the API client.

        Args:
            email: User email for authentication.
            password: User password for authentication.
            timezone: IANA timezone string (e.g., "America/New_York", "Europe/London").
            websession: Shared aiohttp client session for outbound HTTP requests.
        """
        self.email = email
        self.password = password
        self.websession = websession
        self.id_token: str | None = None
        self.refresh_token: str | None = None
        self.user_uid: str | None = None
        self.token_expires_at: float | None = None
        self._firestore_client: AsyncClient | None = None
        self._firestore_client_loop: asyncio.AbstractEventLoop | None = None
        self._listener_client: firestore.Client | None = None
        self._timezone = ZoneInfo(timezone)
        self._listeners: dict = {}  # Store active listeners
        self._listener_callbacks: dict = {}  # Store callbacks to recreate listeners

    async def authenticate(self) -> None:
        """Authenticate with Firebase."""
        _LOGGER.debug("Authenticating with Huckleberry")

        try:
            async with self.websession.post(
                f"{AUTH_URL}?key={FIREBASE_API_KEY}",
                json={
                    "email": self.email,
                    "password": self.password,
                    "returnSecureToken": True,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                await _raise_for_status_with_details(response, "Authentication")
                data = await response.json()
            self.id_token = data["idToken"]
            self.refresh_token = data["refreshToken"]
            self.user_uid = data["localId"]
            self.token_expires_at = datetime.now().timestamp() + int(data["expiresIn"])

            _LOGGER.info("Successfully authenticated with Huckleberry")
        except aiohttp.ClientResponseError as err:
            _LOGGER.error("Authentication failed: status=%s message=%s", err.status, err.message)
            raise
        except aiohttp.ClientError as err:
            _LOGGER.error("Authentication request failed: %s", err)
            raise

    async def ensure_session(self) -> None:
        """Ensure there is a valid authenticated session.

        This should be called periodically (for example by a coordinator) to keep
        long-lived listeners healthy across token expiration windows.
        """
        await self._ensure_authenticated()

    async def refresh_session_token(self) -> None:
        """Refresh the Firebase authentication token."""
        if not self.refresh_token:
            raise ValueError("No refresh token available")

        _LOGGER.debug("Refreshing authentication token")

        try:
            async with self.websession.post(
                f"{REFRESH_URL}?key={FIREBASE_API_KEY}",
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                await _raise_for_status_with_details(response, "Token refresh")
                data = await response.json()
        except aiohttp.ClientError as err:
            _LOGGER.error("Failed to refresh authentication token: %s", err)
            raise
        except ValueError as err:
            _LOGGER.error("Invalid refresh token response payload: %s", err)
            raise

        self.id_token = data["id_token"]
        self.refresh_token = data["refresh_token"]
        self.token_expires_at = datetime.now().timestamp() + int(data["expires_in"])

        # Stop existing listeners (they use the old token)
        for key, watch in self._listeners.items():
            try:
                if hasattr(watch, "unsubscribe") and callable(getattr(watch, "unsubscribe")):
                    watch.unsubscribe()
                elif hasattr(watch, "close") and callable(getattr(watch, "close")):
                    watch.close()
                _LOGGER.debug("Stopped listener %s before token refresh", key)
            except (AttributeError, RuntimeError, TypeError, ValueError) as err:
                _LOGGER.error("Error stopping listener %s before refresh: %s", key, err)
        self._listeners.clear()

        # Invalidate both Firestore clients so they get recreated with new token.
        # The async client is used for reads/writes; the sync client backs the
        # snapshot listeners. Without resetting the sync client, recreated
        # listeners reuse the old credentials and silently stop receiving
        # updates after the first token refresh (~1 hour).
        self._firestore_client = None
        self._firestore_client_loop = None
        self._listener_client = None

        _LOGGER.debug("Successfully refreshed authentication token")

        await self._recreate_listeners()

    async def _ensure_authenticated(self) -> None:
        """Ensure we have a valid authentication token."""
        if not self.id_token:
            await self.authenticate()
        elif self.token_expires_at and datetime.now().timestamp() >= self.token_expires_at - 300:
            # Refresh if token expires in less than 5 minutes
            await self.refresh_session_token()

    async def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        await self._ensure_authenticated()
        return {
            "Authorization": f"Bearer {self.id_token}",
            "Content-Type": "application/json",
        }

    async def _get_firestore_client(self) -> AsyncClient:
        """Get or create Firestore client."""
        await self._ensure_authenticated()
        current_loop = asyncio.get_running_loop()

        # Recreate when missing or when crossing asyncio event loop boundaries.
        # grpc.aio channels are loop-bound and cannot be reused across loops.
        if self._firestore_client and self._firestore_client_loop is not current_loop:
            self._firestore_client = None
            self._firestore_client_loop = None

        if not self._firestore_client:
            assert self.id_token is not None, "id_token should be set after authentication"
            credentials = FirebaseTokenCredentials(self.id_token)
            self._firestore_client = AsyncClient(
                project="simpleintervals",
                credentials=credentials,
            )
            self._firestore_client_loop = current_loop

        return self._firestore_client

    async def _get_timezone_offset_minutes(self) -> float:
        """Get current timezone offset in minutes.

        Calculates offset dynamically to handle DST changes.
        Returns negative for UTC+ timezones (e.g., -120 for UTC+2).
        """
        now = datetime.now(self._timezone)
        offset = now.utcoffset()
        if offset is None:
            return 0.0
        return -offset.total_seconds() / 60

    async def _recreate_listeners(self) -> None:
        """Recreate listeners after a token refresh (implemented by ListenerMixin)."""
