"""Real-time document watches: async subscriptions and the callback shim.

``watch_*()`` returns a :class:`~huckleberry_api.subscriptions.Subscription`
for a document.  ``setup_*_listener()`` keeps the historical callback API and is
implemented as a passthrough sink over the same per-document watch, so both
surfaces share one transport.
"""

from __future__ import annotations

import warnings
from typing import Any, Callable, Literal, TypeVar, cast

from google.cloud import firestore

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
from .subscriptions import Snapshot
from .subscriptions import Subscription
from .subscriptions import _WatchHub

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

CollectionName = Literal["sleep", "feed", "health", "diaper", "activities", "pump", "childs"]

_PARSERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "sleep": FirebaseSleepDocumentData.model_validate,
    "feed": FirebaseFeedDocumentData.model_validate,
    "health": FirebaseHealthDocumentData.model_validate,
    "diaper": FirebaseDiaperDocumentData.model_validate,
    "activities": FirebaseActivityDocumentData.model_validate,
    "pump": FirebasePumpDocumentData.model_validate,
    "childs": FirebaseChildDocument.model_validate,
}


class ListenerMixin(HuckleberryBase):
    """Real-time snapshot listeners and async subscriptions."""

    # -- transport --------------------------------------------------------

    def _create_watch_client(self, token: str) -> firestore.Client:
        """Build a sync client for watches; a new one is required per token."""
        return firestore.Client(
            project="simpleintervals",
            credentials=FirebaseTokenCredentials(token),
        )

    async def _get_watch_hub(self) -> _WatchHub:
        await self._ensure_authenticated()
        if self._watch_hub is None:
            self._watch_hub = _WatchHub(self._create_watch_client)
        assert self.id_token is not None, "id_token should be set after authentication"
        hub = cast(_WatchHub, self._watch_hub)
        await hub.ensure_client(self.id_token)
        return hub

    async def _watch_handle(
        self,
        collection: str,
        child_uid: str,
        parser: Callable[[dict[str, Any]], Any],
        *,
        for_subscription: bool = True,
    ) -> Any:
        hub = await self._get_watch_hub()
        return hub.handle(collection, child_uid, parser, for_subscription=for_subscription)

    async def _on_credentials_refreshed(self) -> None:
        """Reopen every watch with the refreshed token, keeping subscribers."""
        if self._watch_hub is None:
            return
        assert self.id_token is not None, "id_token should be set after refresh"
        await cast(_WatchHub, self._watch_hub).rotate(self.id_token)

    # -- async subscriptions ---------------------------------------------

    def watch_sleep(self, child_uid: str) -> Subscription[FirebaseSleepDocumentData]:
        """Subscribe to ``sleep/{child_uid}``.  No I/O until first use."""
        return Subscription(self, "sleep", child_uid, _PARSERS["sleep"])

    def watch_feed(self, child_uid: str) -> Subscription[FirebaseFeedDocumentData]:
        """Subscribe to ``feed/{child_uid}``.  No I/O until first use."""
        return Subscription(self, "feed", child_uid, _PARSERS["feed"])

    def watch_health(self, child_uid: str) -> Subscription[FirebaseHealthDocumentData]:
        """Subscribe to ``health/{child_uid}``.  No I/O until first use."""
        return Subscription(self, "health", child_uid, _PARSERS["health"])

    def watch_diaper(self, child_uid: str) -> Subscription[FirebaseDiaperDocumentData]:
        """Subscribe to ``diaper/{child_uid}``.  No I/O until first use."""
        return Subscription(self, "diaper", child_uid, _PARSERS["diaper"])

    def watch_activity(self, child_uid: str) -> Subscription[FirebaseActivityDocumentData]:
        """Subscribe to ``activities/{child_uid}``.  No I/O until first use."""
        return Subscription(self, "activities", child_uid, _PARSERS["activities"])

    def watch_pump(self, child_uid: str) -> Subscription[FirebasePumpDocumentData]:
        """Subscribe to ``pump/{child_uid}``.  No I/O until first use."""
        return Subscription(self, "pump", child_uid, _PARSERS["pump"])

    def watch_child(self, child_uid: str) -> Subscription[FirebaseChildDocument]:
        """Subscribe to ``childs/{child_uid}``.  No I/O until first use."""
        return Subscription(self, "childs", child_uid, _PARSERS["childs"])

    # -- deprecated callback API -----------------------------------------

    async def _setup_listener(
        self,
        collection_name: CollectionName,
        child_uid: str,
        callback: Callable[[TDocumentData], None],
    ) -> None:
        """Register a callback sink on the document's shared watch."""
        warnings.warn(
            "setup_*_listener() is deprecated; use watch_*() and async for instead",
            DeprecationWarning,
            stacklevel=2,
        )
        _LOGGER.info("Setting up real-time listener for %s/%s", collection_name, child_uid)
        handle = await self._watch_handle(
            collection_name, child_uid, _PARSERS[collection_name], for_subscription=False
        )

        def sink(snapshot: Snapshot[Any]) -> None:
            if not snapshot.exists:
                return
            callback(cast(TDocumentData, snapshot.value))

        handle.add_sink(sink)
        self._listener_sinks.append((handle, sink))
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
        """Stop every callback listener and every subscription on this client.

        Kept for compatibility.  Pending ``__anext__``/``ready()`` calls end with
        ``StopAsyncIteration``; a later registration starts a fresh hub.
        """
        _LOGGER.info("Stopping all real-time listeners")
        for handle, sink in list(self._listener_sinks):
            handle.remove_sink(sink)
        self._listener_sinks.clear()
        hub = self._watch_hub
        self._watch_hub = None
        if hub is not None:
            await cast(_WatchHub, hub).stop()
