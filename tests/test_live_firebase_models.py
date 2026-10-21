"""Live Firebase schema validation tests.

Run actions in the app first, then run this module to validate latest entries
against strict schemas in `huckleberry_api.firebase_types`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import pytest
from google.api_core.exceptions import FailedPrecondition
from google.cloud import firestore

from huckleberry_api import HuckleberryAPI
from huckleberry_api.api import CURATED_FOODS_BUCKET, CURATED_FOODS_OBJECT
from huckleberry_api.firebase_types import (
    FirebaseActivityDocumentData,
    FirebaseActivityIntervalData,
    FirebaseActivityMultiContainer,
    FirebaseActivityPrefs,
    FirebaseActivityTimerData,
    FirebaseBottleFeedIntervalData,
    FirebaseChildDocument,
    FirebaseCuratedFoodDocument,
    FirebaseCustomFoodTypeDocument,
    FirebaseDiaperData,
    FirebaseDiaperDocumentData,
    FirebaseDiaperMultiContainer,
    FirebaseFeedDocumentData,
    FirebaseFeedMultiContainer,
    FirebaseGrowthData,
    FirebaseHealthDocumentData,
    FirebaseHealthMultiContainer,
    FirebaseLastPumpData,
    FirebaseMedicationData,
    FirebasePumpDocumentData,
    FirebasePumpIntervalData,
    FirebasePumpMultiContainer,
    FirebasePumpPrefs,
    FirebasePumpTimerData,
    FirebaseSleepDocumentData,
    FirebaseSleepIntervalData,
    FirebaseSleepMultiContainer,
    FirebaseSolidsFeedIntervalData,
    FirebaseSolidsMultiContainer,
    FirebaseTemperatureData,
    FirebaseTypesDocument,
    FirebaseUserDocument,
)


def _max_docs() -> int:
    return int(os.getenv("HUCKLEBERRY_MODEL_VALIDATION_MAX_DOCS", "20"))


def _as_obj_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): val for key, val in value.items()}


def _doc_to_dict(doc: Any) -> dict[str, object] | None:
    to_dict = getattr(doc, "to_dict", None)
    if not callable(to_dict):
        return None
    return _as_obj_dict(to_dict())


async def _iter_latest_by_doc_id(collection_ref: Any, max_docs: int) -> AsyncIterator[Any]:
    queries = [
        collection_ref.order_by("__name__", direction=firestore.Query.DESCENDING).limit(max_docs),
        collection_ref.limit(max_docs),
    ]
    for query in queries:
        try:
            async for doc in query.stream():
                yield doc
            return
        except FailedPrecondition:
            continue


async def _iter_latest_by_field(collection_ref: Any, max_docs: int, field_name: str) -> AsyncIterator[Any]:
    queries = [
        collection_ref.order_by(field_name, direction=firestore.Query.DESCENDING).limit(max_docs),
        collection_ref.limit(max_docs),
    ]
    for query in queries:
        try:
            async for doc in query.stream():
                yield doc
            return
        except FailedPrecondition:
            continue


def _is_multi_wrapper(payload: dict[str, object]) -> bool:
    return payload.get("multi") is True and isinstance(payload.get("data"), dict)


async def _child_uids(api: HuckleberryAPI, db: Any) -> list[str]:
    if not api.user_uid:
        raise RuntimeError("Missing authenticated user UID")

    user_doc = await db.collection("users").document(api.user_uid).get()
    user_payload = _doc_to_dict(user_doc)
    if not user_payload:
        user_model = await api.get_user()
        return [item.cid for item in user_model.childList] if user_model else []

    FirebaseUserDocument.model_validate(user_payload)

    child_uids: list[str] = []
    child_list = user_payload.get("childList")
    if isinstance(child_list, list):
        for item in child_list:
            item_dict = _as_obj_dict(item)
            if item_dict:
                child_id = item_dict.get("cid")
                if isinstance(child_id, str) and child_id:
                    child_uids.append(child_id)

    if not child_uids:
        user_model = await api.get_user()
        child_uids = [item.cid for item in user_model.childList] if user_model else []

    unique_ids: list[str] = []
    seen: set[str] = set()
    for child_uid in child_uids:
        if child_uid in seen:
            continue
        seen.add(child_uid)
        unique_ids.append(child_uid)

    return unique_ids


@pytest.mark.integration
async def test_live_user_child_and_root_documents(api: HuckleberryAPI) -> None:
    """Validate core user/child root docs against strict schemas."""
    db = await api._get_firestore_client()
    child_uids = await _child_uids(api, db)
    assert child_uids

    for child_uid in child_uids:
        child_doc = await db.collection("childs").document(child_uid).get()
        child_payload = _doc_to_dict(child_doc)
        if child_payload:
            FirebaseChildDocument.model_validate(child_payload)

        types_doc = await db.collection("types").document(child_uid).get()
        types_payload = _doc_to_dict(types_doc)
        if types_payload:
            FirebaseTypesDocument.model_validate(types_payload)

        sleep_doc = await db.collection("sleep").document(child_uid).get()
        sleep_payload = _doc_to_dict(sleep_doc)
        if sleep_payload:
            FirebaseSleepDocumentData.model_validate(sleep_payload)

        feed_doc = await db.collection("feed").document(child_uid).get()
        feed_payload = _doc_to_dict(feed_doc)
        if feed_payload:
            FirebaseFeedDocumentData.model_validate(feed_payload)

        diaper_doc = await db.collection("diaper").document(child_uid).get()
        diaper_payload = _doc_to_dict(diaper_doc)
        if diaper_payload:
            FirebaseDiaperDocumentData.model_validate(diaper_payload)

        health_doc = await db.collection("health").document(child_uid).get()
        health_payload = _doc_to_dict(health_doc)
        if health_payload:
            FirebaseHealthDocumentData.model_validate(health_payload)

        pump_doc = await db.collection("pump").document(child_uid).get()
        pump_payload = _doc_to_dict(pump_doc)
        if pump_payload:
            pump_model = FirebasePumpDocumentData.model_validate(pump_payload)

            pump_prefs_payload = _as_obj_dict(pump_payload.get("prefs"))
            if pump_prefs_payload:
                FirebasePumpPrefs.model_validate(pump_prefs_payload)
                if pump_model.prefs and pump_model.prefs.lastPump is not None:
                    FirebaseLastPumpData.model_validate(
                        pump_model.prefs.lastPump.model_dump(by_alias=True, exclude_none=True)
                    )

            pump_timer_payload = _as_obj_dict(pump_payload.get("timer"))
            if pump_timer_payload:
                FirebasePumpTimerData.model_validate(pump_timer_payload)


@pytest.mark.integration
async def test_live_latest_sleep_and_diaper_intervals(api: HuckleberryAPI) -> None:
    """Validate latest sleep/diaper intervals (regular and multi wrapper docs)."""
    db = await api._get_firestore_client()
    child_uids = await _child_uids(api, db)

    for child_uid in child_uids:
        sleep_ref = db.collection("sleep").document(child_uid).collection("intervals")
        async for doc in _iter_latest_by_doc_id(sleep_ref, _max_docs()):
            payload = _doc_to_dict(doc)
            if not payload:
                continue
            if _is_multi_wrapper(payload):
                FirebaseSleepMultiContainer.model_validate(payload)
            else:
                FirebaseSleepIntervalData.model_validate(payload)

        diaper_ref = db.collection("diaper").document(child_uid).collection("intervals")
        async for doc in _iter_latest_by_doc_id(diaper_ref, _max_docs()):
            payload = _doc_to_dict(doc)
            if not payload:
                continue
            if _is_multi_wrapper(payload):
                FirebaseDiaperMultiContainer.model_validate(payload)
            else:
                FirebaseDiaperData.model_validate(payload)


@pytest.mark.integration
async def test_live_latest_feed_intervals(api: HuckleberryAPI) -> None:
    """Validate latest feed intervals by mode (breast/bottle/solids)."""
    db = await api._get_firestore_client()
    child_uids = await _child_uids(api, db)

    for child_uid in child_uids:
        feed_ref = db.collection("feed").document(child_uid).collection("intervals")
        async for doc in _iter_latest_by_doc_id(feed_ref, _max_docs()):
            payload = _doc_to_dict(doc)
            if not payload:
                continue

            if _is_multi_wrapper(payload):
                FirebaseFeedMultiContainer.model_validate(payload)
                multi_data = payload.get("data")
                if isinstance(multi_data, dict):
                    all_solids = bool(multi_data) and all(
                        (entry_dict := _as_obj_dict(entry)) is not None and entry_dict.get("mode") == "solids"
                        for entry in multi_data.values()
                    )
                    if all_solids:
                        FirebaseSolidsMultiContainer.model_validate(payload)

                    for entry in multi_data.values():
                        entry_dict = _as_obj_dict(entry)
                        if not entry_dict:
                            continue
                        mode = entry_dict.get("mode")
                        if mode == "solids":
                            FirebaseSolidsFeedIntervalData.model_validate(entry_dict)
                        elif mode == "bottle":
                            FirebaseBottleFeedIntervalData.model_validate(entry_dict)
                continue

            mode = payload.get("mode")
            if mode == "solids":
                FirebaseSolidsFeedIntervalData.model_validate(payload)
            elif mode == "bottle":
                FirebaseBottleFeedIntervalData.model_validate(payload)


@pytest.mark.integration
async def test_live_latest_health_pump_activities_and_foods(api: HuckleberryAPI) -> None:
    """Validate latest health/pump/activities/custom+curated food payloads."""
    db = await api._get_firestore_client()
    child_uids = await _child_uids(api, db)

    for child_uid in child_uids:
        health_ref = db.collection("health").document(child_uid).collection("data")
        async for doc in _iter_latest_by_doc_id(health_ref, _max_docs()):
            payload = _doc_to_dict(doc)
            if not payload:
                continue
            if _is_multi_wrapper(payload):
                FirebaseHealthMultiContainer.model_validate(payload)
                continue
            mode = payload.get("mode")
            if mode == "growth":
                FirebaseGrowthData.model_validate(payload)
            elif mode == "medication":
                FirebaseMedicationData.model_validate(payload)
            elif mode == "temperature":
                FirebaseTemperatureData.model_validate(payload)

        # Batch documents can fall outside the latest-by-ID sample (the ordered
        # query requires a composite index), so validate every health batch.
        # The app writes these documents and the wrapper only ever reads them.
        async for doc in health_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream():
            payload = _doc_to_dict(doc)
            if payload:
                FirebaseHealthMultiContainer.model_validate(payload)

        custom_ref = db.collection("types").document(child_uid).collection("custom")
        async for doc in _iter_latest_by_field(custom_ref, _max_docs(), "updated_at"):
            payload = _doc_to_dict(doc)
            if not payload:
                continue
            FirebaseCustomFoodTypeDocument.model_validate(payload)

        pump_ref = db.collection("pump").document(child_uid).collection("intervals")
        async for doc in _iter_latest_by_doc_id(pump_ref, _max_docs()):
            payload = _doc_to_dict(doc)
            if not payload:
                continue
            if _is_multi_wrapper(payload):
                pump_multi = FirebasePumpMultiContainer.model_validate(payload)
                for entry in pump_multi.data.values():
                    FirebasePumpIntervalData.model_validate(entry.model_dump(by_alias=True, exclude_none=True))
            else:
                FirebasePumpIntervalData.model_validate(payload)

        activities_doc = await db.collection("activities").document(child_uid).get()
        activities_payload = _doc_to_dict(activities_doc)
        if activities_payload:
            FirebaseActivityDocumentData.model_validate(activities_payload)

            activities_prefs_payload = _as_obj_dict(activities_payload.get("prefs"))
            if activities_prefs_payload:
                FirebaseActivityPrefs.model_validate(activities_prefs_payload)

            activities_timer_payload = _as_obj_dict(activities_payload.get("timer"))
            if activities_timer_payload:
                FirebaseActivityTimerData.model_validate(activities_timer_payload)

        activities_ref = db.collection("activities").document(child_uid).collection("intervals")
        async for doc in _iter_latest_by_doc_id(activities_ref, _max_docs()):
            payload = _doc_to_dict(doc)
            if not payload:
                continue
            if _is_multi_wrapper(payload):
                FirebaseActivityMultiContainer.model_validate(payload)
            else:
                FirebaseActivityIntervalData.model_validate(payload)

    if not api.id_token:
        raise RuntimeError("Missing id_token after authentication")

    encoded_object = quote(CURATED_FOODS_OBJECT, safe="")
    url = f"https://firebasestorage.googleapis.com/v0/b/{CURATED_FOODS_BUCKET}/o/{encoded_object}?alt=media"
    async with api.websession.get(url, headers={"Authorization": f"Bearer {api.id_token}"}, timeout=30) as response:
        response.raise_for_status()
        payload = await response.json()

    assert isinstance(payload, dict)
    for raw_food in payload.values():
        if not isinstance(raw_food, dict):
            continue
        entry = dict(raw_food)
        FirebaseCuratedFoodDocument.model_validate(entry)
