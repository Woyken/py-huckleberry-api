"""Solids tracker: food catalog, custom foods, and saved meals."""

from __future__ import annotations

import aiohttp
import time
import uuid
from datetime import datetime
from datetime import timezone as dt_timezone
from urllib.parse import quote
from .client import HuckleberryBase
from .common import _LOGGER
from .const import CURATED_FOODS_BUCKET
from .const import CURATED_FOODS_OBJECT
from .firebase_types import FirebaseCuratedFoodDocument
from .firebase_types import FirebaseCustomFoodTypeDocument
from .firebase_types import FirebaseFeedDocumentData
from .firebase_types import FirebaseLastSolidData
from .firebase_types import FirebaseSolidsFeedIntervalData
from .firebase_types import FirebaseTypesAvailableTypes
from .firebase_types import FirebaseTypesDocument
from .firebase_types import SolidsFoodEntry
from .firebase_types import SolidsReaction
from .firebase_types import to_firebase_dict
from .models import SolidsFoodReference


class SolidsMixin(HuckleberryBase):
    """Solids food catalog and meal methods."""

    async def list_solids_curated_foods(self) -> list[FirebaseCuratedFoodDocument]:
        """List curated solids foods from Firebase Storage."""
        await self._ensure_authenticated()

        if not self.id_token:
            raise RuntimeError("Missing authentication token")

        encoded_object = quote(CURATED_FOODS_OBJECT, safe="")
        url = f"https://firebasestorage.googleapis.com/v0/b/{CURATED_FOODS_BUCKET}/o/{encoded_object}?alt=media"

        async with self.websession.get(
            url,
            headers={"Authorization": f"Bearer {self.id_token}"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected curated foods payload shape")

        foods: list[FirebaseCuratedFoodDocument] = []
        for food_data in payload.values():
            if not isinstance(food_data, dict):
                continue

            entry = dict(food_data)
            foods.append(FirebaseCuratedFoodDocument.model_validate(entry))

        return sorted(
            foods,
            key=lambda item: (
                float(item.rank) if item.rank is not None else float("inf"),
                item.name.lower(),
            ),
        )

    async def list_solids_custom_foods(
        self, child_uid: str, include_archived: bool = False
    ) -> list[FirebaseCustomFoodTypeDocument]:
        """List custom solids foods from Firestore ``types/{child_uid}/custom``."""
        client = await self._get_firestore_client()
        custom_ref = client.collection("types").document(child_uid).collection("custom")

        foods: list[FirebaseCustomFoodTypeDocument] = []
        async for doc in custom_ref.where("type", "==", "solids").stream():
            raw_data = doc.to_dict() or {}
            item = FirebaseCustomFoodTypeDocument.model_validate(raw_data)
            if not include_archived and item.archived:
                continue
            foods.append(item)

        return sorted(foods, key=lambda item: item.updated_at, reverse=True)

    async def create_solids_custom_food(
        self, child_uid: str, name: str, image: str = ""
    ) -> FirebaseCustomFoodTypeDocument:
        """Create a custom solids food in types/{child_uid}/custom."""
        food_name = name.strip()
        if not food_name:
            raise ValueError("Custom food name must be non-empty")

        now_iso = datetime.now(dt_timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        food_id = str(uuid.uuid4())

        custom_food = FirebaseCustomFoodTypeDocument(
            created_at=now_iso,
            updated_at=now_iso,
            name=food_name,
            archived=False,
            id=food_id,
            type="solids",
            image=image,
            source="custom",
        )

        client = await self._get_firestore_client()
        types_ref = client.collection("types").document(child_uid)
        types_document = FirebaseTypesDocument(available_types=FirebaseTypesAvailableTypes(solids=True))
        await types_ref.set(to_firebase_dict(types_document), merge=True)
        await types_ref.collection("custom").document(food_id).set(to_firebase_dict(custom_food))

        return custom_food

    async def log_solids(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        foods: list[SolidsFoodReference],
        notes: str = "",
        reaction: SolidsReaction | None = None,
        food_note_image: str | None = None,
    ) -> None:
        """Log solid food feeding.

        Args:
            child_uid: Child unique identifier
            start_time: Event time
            foods: Existing food references with explicit id/source/name/amount
            notes: Optional notes about the meal
            reaction: Optional reaction - "LOVED", "MEH", "HATED", or "ALLERGIC"
            food_note_image: Optional Firebase Storage image filename
        """
        if not foods:
            raise ValueError("At least one food is required")

        _LOGGER.info("Logging solids for child %s with %d foods", child_uid, len(foods))

        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)

        start_timestamp = start_time.timestamp()
        current_time = time.time()
        interval_id = f"{int(current_time * 1000)}-{uuid.uuid4().hex[:20]}"
        offset = await self._get_timezone_offset_minutes()
        feed_doc = await feed_ref.get()
        feed_model = FirebaseFeedDocumentData.model_validate(feed_doc.to_dict() or {})
        existing_last_solid = feed_model.prefs.lastSolid if feed_model.prefs else None
        existing_last_solid_start = existing_last_solid.start if existing_last_solid else None
        should_update_last_solid = existing_last_solid_start is None or start_timestamp >= float(
            existing_last_solid_start
        )

        foods_dict: dict[str, SolidsFoodEntry] = {}
        for food_item in foods:
            food_ref = (
                food_item
                if isinstance(food_item, SolidsFoodReference)
                else SolidsFoodReference.model_validate(food_item)
            )

            food_name = food_ref.name.strip()
            if not food_name:
                raise ValueError("Food name must be non-empty")

            foods_dict[food_ref.id] = SolidsFoodEntry(
                id=food_ref.id,
                source=food_ref.source,
                created_name=food_name,
                amount=food_ref.amount,
            )

        entry = FirebaseSolidsFeedIntervalData(
            mode="solids",
            start=start_timestamp,
            lastUpdated=current_time,
            offset=offset,
            foods=foods_dict,
        )

        if notes:
            entry.notes = notes
        if reaction:
            entry.reactions = {reaction: True}
        if food_note_image:
            entry.foodNoteImage = food_note_image

        await feed_ref.collection("intervals").document(interval_id).set(to_firebase_dict(entry))

        last_solid = FirebaseLastSolidData(
            mode="solids",
            start=start_timestamp,
            foods=foods_dict,
            reactions={reaction: True} if reaction else None,
            notes=notes if notes else None,
            offset=offset,
        )

        if should_update_last_solid:
            await feed_ref.set(
                {
                    "prefs": {
                        "lastSolid": to_firebase_dict(last_solid),
                        "timestamp": {"seconds": current_time},
                        "local_timestamp": current_time,
                    }
                },
                merge=True,
            )

        _LOGGER.info("Solids logged with %d references (updated_last=%s)", len(foods_dict), should_update_last_solid)
