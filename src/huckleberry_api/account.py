"""Child and user profile reads."""

from __future__ import annotations

from google.api_core.exceptions import GoogleAPICallError
from pydantic import ValidationError
from .client import HuckleberryBase
from .common import _LOGGER
from .firebase_types import FirebaseChildDocument
from .firebase_types import FirebaseUserDocument


class AccountMixin(HuckleberryBase):
    """Child and user profile lookups."""

    async def get_child(self, child_uid: str) -> FirebaseChildDocument | None:
        """Get a single child document by child UID."""
        _LOGGER.debug("Fetching child document for %s", child_uid)

        try:
            db = await self._get_firestore_client()
            child_doc_ref = db.collection("childs").document(child_uid)
            child_doc = await child_doc_ref.get()

            if not child_doc.exists:
                _LOGGER.warning("Child document not found: %s", child_uid)
                return None

            child_data = child_doc.to_dict()
            if not child_data:
                _LOGGER.warning("Child document has no data: %s", child_uid)
                return None

            return FirebaseChildDocument.model_validate(child_data)

        except (GoogleAPICallError, ValidationError, RuntimeError, TypeError, ValueError) as err:
            _LOGGER.error("Failed to get child %s: %s", child_uid, err)
            raise

    async def get_user(self) -> FirebaseUserDocument | None:
        """Get full users/{uid} document as strict Firebase model."""
        _LOGGER.debug("Fetching user document")

        db = await self._get_firestore_client()
        user_ref = db.collection("users").document(self.user_uid)
        user_doc = await user_ref.get()

        if not user_doc.exists:
            _LOGGER.error("User document not found")
            return None

        user_data = user_doc.to_dict()
        if not user_data:
            _LOGGER.error("User document has no data")
            return None

        return FirebaseUserDocument.model_validate(user_data)
