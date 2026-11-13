"""Real-time Firestore listeners and token-refresh recreation."""

from __future__ import annotations

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import firestore
from typing import Callable
from typing import Literal
from typing import TypeVar
from typing import cast
from ._client import FirebaseTokenCredentials
from ._client import HuckleberryBase
from ._common import _LOGGER
from .firebase_types import FirebaseActivityDocumentData
from .firebase_types import FirebaseChildDocument
from .firebase_types import FirebaseDiaperDocumentData
from .firebase_types import FirebaseFeedDocumentData
from .firebase_types import FirebaseHealthDocumentData
from .firebase_types import FirebasePumpDocumentData
from .firebase_types import FirebaseSleepDocumentData


TDocumentData = TypeVar(
    "TDocumentData",
    FirebaseSleepDocumentData,
    FirebaseFeedDocumentData,
    FirebaseHealthDocumentData,
    FirebaseDiaperDocumentData,
    FirebaseActivityDocumentData,
    FirebasePumpDocumentData,
    FirebaseChildDocument,
)


class ListenerMixin(HuckleberryBase):
    """Real-time snapshot listener methods."""

    async def _setup_listener(
        self,
        collection_name: Literal["sleep", "feed", "health", "diaper", "activities", "pump", "childs"],
        child_uid: str,
        callback: Callable[[TDocumentData], None],
    ) -> None:
        """Set up real-time listener for a Firestore document.

        Generic listener setup method that works for any collection type.

        Args:
            collection_name: Name of the Firestore collection (e.g., 'sleep', 'feed', 'health', 'diaper')
            child_uid: Child unique identifier
            callback: Function to call when document changes, receives document data of the appropriate type
        """
        _LOGGER.info("Setting up real-time listener for %s/%s", collection_name, child_uid)

        await self._ensure_authenticated()
        if not self._listener_client:
            assert self.id_token is not None, "id_token should be set after authentication"
            credentials = FirebaseTokenCredentials(self.id_token)
            self._listener_client = firestore.Client(
                project="simpleintervals",
                credentials=credentials,
            )

        doc_ref = self._listener_client.collection(collection_name).document(child_uid)

        # Create snapshot listener
        def on_snapshot(doc_snapshot, _changes, _read_time):
            """Handle snapshot updates."""
            for doc in doc_snapshot:
                if doc.exists:
                    _LOGGER.debug("Real-time %s update received for child %s", collection_name, child_uid)
                    payload = doc.to_dict() or {}
                    if collection_name == "sleep":
                        validated = FirebaseSleepDocumentData.model_validate(payload)
                    elif collection_name == "feed":
                        validated = FirebaseFeedDocumentData.model_validate(payload)
                    elif collection_name == "health":
                        validated = FirebaseHealthDocumentData.model_validate(payload)
                    elif collection_name == "activities":
                        validated = FirebaseActivityDocumentData.model_validate(payload)
                    elif collection_name == "pump":
                        validated = FirebasePumpDocumentData.model_validate(payload)
                    elif collection_name == "childs":
                        validated = FirebaseChildDocument.model_validate(payload)
                    else:
                        validated = FirebaseDiaperDocumentData.model_validate(payload)
                    callback(cast(TDocumentData, validated))

        # Start listening and store the unsubscribe function
        unsubscribe = doc_ref.on_snapshot(on_snapshot)
        listener_key = f"{collection_name}_{child_uid}"
        self._listeners[listener_key] = unsubscribe
        # Store callback for recreation after token refresh
        self._listener_callbacks[listener_key] = (collection_name, child_uid, callback)

        _LOGGER.info("Real-time %s listener active for child %s", collection_name, child_uid)

    async def setup_sleep_listener(self, child_uid: str, callback: Callable[[FirebaseSleepDocumentData], None]) -> None:
        """Set up real-time listener for sleep document changes."""
        await self._setup_listener("sleep", child_uid, callback)

    async def setup_feed_listener(self, child_uid: str, callback: Callable[[FirebaseFeedDocumentData], None]) -> None:
        """Set up real-time listener for feed document changes."""
        await self._setup_listener("feed", child_uid, callback)

    async def setup_health_listener(
        self, child_uid: str, callback: Callable[[FirebaseHealthDocumentData], None]
    ) -> None:
        """Set up real-time listener for health document changes."""
        await self._setup_listener("health", child_uid, callback)

    async def setup_diaper_listener(
        self, child_uid: str, callback: Callable[[FirebaseDiaperDocumentData], None]
    ) -> None:
        """Set up real-time listener for diaper document changes."""
        await self._setup_listener("diaper", child_uid, callback)

    async def setup_activity_listener(
        self, child_uid: str, callback: Callable[[FirebaseActivityDocumentData], None]
    ) -> None:
        """Set up real-time listener for activities document changes."""
        await self._setup_listener("activities", child_uid, callback)

    async def setup_pump_listener(self, child_uid: str, callback: Callable[[FirebasePumpDocumentData], None]) -> None:
        """Set up real-time listener for pump document changes."""
        await self._setup_listener("pump", child_uid, callback)

    async def setup_child_listener(self, child_uid: str, callback: Callable[[FirebaseChildDocument], None]) -> None:
        """Set up real-time listener for child document changes."""
        await self._setup_listener("childs", child_uid, callback)

    async def stop_all_listeners(self) -> None:
        """Stop all active real-time listeners."""
        _LOGGER.info("Stopping all real-time listeners")
        for key, watch in self._listeners.items():
            try:
                if hasattr(watch, "unsubscribe") and callable(getattr(watch, "unsubscribe")):
                    watch.unsubscribe()
                elif hasattr(watch, "close") and callable(getattr(watch, "close")):
                    watch.close()
                else:
                    _LOGGER.debug("Listener %s object has no unsubscribe/close", key)
                _LOGGER.debug("Stopped listener: %s", key)
            except (AttributeError, RuntimeError, TypeError, ValueError) as err:
                _LOGGER.error("Error stopping listener %s: %s", key, err)
        self._listeners.clear()
        self._listener_callbacks.clear()
        self._listener_client = None

    async def _recreate_listeners(self) -> None:
        """Recreate every registered listener with the refreshed token."""
        _LOGGER.info("Recreating %d listeners with refreshed token", len(self._listener_callbacks))
        callbacks_copy = dict(self._listener_callbacks)  # Copy to avoid modification during iteration
        for key, (listener_type, child_uid, callback) in callbacks_copy.items():
            try:
                if listener_type == "sleep":
                    await self.setup_sleep_listener(child_uid, callback)
                elif listener_type == "feed":
                    await self.setup_feed_listener(child_uid, callback)
                elif listener_type == "health":
                    await self.setup_health_listener(child_uid, callback)
                elif listener_type == "diaper":
                    await self.setup_diaper_listener(child_uid, callback)
                elif listener_type == "activities":
                    await self.setup_activity_listener(child_uid, callback)
                elif listener_type == "pump":
                    await self.setup_pump_listener(child_uid, callback)
                elif listener_type == "childs":
                    await self.setup_child_listener(child_uid, callback)
                _LOGGER.debug("Recreated %s listener for child %s", listener_type, child_uid)
            except (GoogleAPICallError, RuntimeError, TypeError, ValueError) as err:
                _LOGGER.error("Error recreating %s listener for child %s: %s", listener_type, child_uid, err)
