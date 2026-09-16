"""Milestone catalog and creation tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from huckleberry_api import HuckleberryAPI, PredefinedMilestone
from huckleberry_api.firebase_types import (
    FirebaseChildDocument,
    FirebaseCustomMilestoneData,
    FirebasePredefinedMilestoneData,
)
from huckleberry_api.models import MilestoneRecord


def _milestone_write_client():
    interval_document = MagicMock()
    interval_document.set = AsyncMock()

    duplicate_query = MagicMock()
    intervals = MagicMock()
    intervals.document.return_value = interval_document
    intervals.where.return_value.limit.return_value = duplicate_query

    root_document = MagicMock()
    root_document.collection.return_value = intervals

    client = MagicMock()
    client.collection.return_value.document.return_value = root_document
    return client, intervals, interval_document, duplicate_query


def test_predefined_catalog_matches_verified_apk_snapshot() -> None:
    """The bundled catalog should preserve all unique APK 0.9.305 entries."""
    milestones = HuckleberryAPI.list_predefined_milestones()

    assert len(milestones) == 83
    assert len({milestone.id for milestone in milestones}) == 83
    assert {milestone.category for milestone in milestones} == {
        "cognitive",
        "language",
        "memory",
        "movement",
        "social",
    }
    assert {milestone.source for milestone in milestones} == {"CDC", "Huckleberry"}

    copies_chores = next(
        milestone for milestone in milestones if milestone.id == "cdc-18-cognitive-copies-you-doing-chores"
    )
    assert copies_chores == PredefinedMilestone(
        id="cdc-18-cognitive-copies-you-doing-chores",
        age_months=18,
        category="cognitive",
        title="Copies chores",
        typical_window="15–20 months",
        source="CDC",
    )


def test_predefined_catalog_entries_are_immutable() -> None:
    """Consumers must not be able to alter catalog metadata in place."""
    milestone = HuckleberryAPI.list_predefined_milestones()[0]

    with pytest.raises(ValidationError):
        milestone.title = "Changed"


def test_firebase_milestone_models_reject_mixed_shapes() -> None:
    """Custom rows cannot carry only part of the predefined metadata group."""
    common = {
        "start": 1_789_590_963.384,
        "offset": -180.0,
        "name": "Copies chores",
        "lastUpdated": 1_789_590_973.865,
    }

    custom = FirebaseCustomMilestoneData.model_validate(common)
    assert custom.notes is None
    assert custom.photo is None

    predefined = FirebasePredefinedMilestoneData.model_validate(
        {
            **common,
            "milestoneId": "cdc-18-cognitive-copies-you-doing-chores",
            "milestoneCategory": "cognitive",
            "milestoneAgeRange": "15–20 months",
            "milestoneSource": "CDC",
        }
    )
    assert predefined.milestoneCategory == "cognitive"
    assert not isinstance(predefined, FirebaseCustomMilestoneData)

    with pytest.raises(ValidationError):
        FirebaseCustomMilestoneData.model_validate({**common, "milestoneId": "unexpected"})

    with pytest.raises(ValidationError):
        FirebasePredefinedMilestoneData.model_validate({**common, "milestoneId": "incomplete"})


async def test_create_custom_milestone_writes_minimal_app_shape(websession, monkeypatch) -> None:
    """Custom creation should trim text and omit empty optional fields."""
    api = HuckleberryAPI("test", "test", "Europe/Vilnius", websession)
    client, _, interval_document, _ = _milestone_write_client()
    occurred_at = datetime(2026, 9, 15, 12, 30, tzinfo=timezone(timedelta(hours=3)))

    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=client))
    monkeypatch.setattr(api, "_validate_milestone_time", AsyncMock(return_value=occurred_at))
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_592_886.952)
    monkeypatch.setattr("huckleberry_api.api.secrets.token_hex", lambda _: "12c8568cd9071266a18f")

    created = await api.create_custom_milestone(
        "child",
        name="  First API milestone  ",
        start_time=occurred_at,
        notes="   ",
    )

    assert created.id == "1789592886952-12c8568cd9071266a18f"
    assert created.milestone == FirebaseCustomMilestoneData(
        start=occurred_at.timestamp(),
        offset=-180.0,
        name="First API milestone",
        lastUpdated=1_789_592_886.952,
    )
    interval_document.set.assert_awaited_once_with(
        {
            "start": occurred_at.timestamp(),
            "offset": -180.0,
            "name": "First API milestone",
            "lastUpdated": 1_789_592_886.952,
        }
    )


async def test_create_custom_milestone_rejects_blank_name_before_firebase(websession, monkeypatch) -> None:
    """Whitespace-only custom names are disabled by the app and rejected by the API."""
    api = HuckleberryAPI("test", "test", "UTC", websession)
    get_client = AsyncMock()
    monkeypatch.setattr(api, "_get_firestore_client", get_client)

    with pytest.raises(ValueError, match="non-empty"):
        await api.create_custom_milestone("child", name=" \t ", start_time=datetime.now(timezone.utc))

    get_client.assert_not_awaited()


async def test_create_predefined_milestone_writes_catalog_metadata(websession, monkeypatch) -> None:
    """Predefined creation should derive every immutable field from the catalog."""
    api = HuckleberryAPI("test", "test", "Europe/Vilnius", websession)
    client, _, interval_document, duplicate_query = _milestone_write_client()
    duplicate_query.get = AsyncMock(return_value=[])
    occurred_at = datetime(2026, 9, 16, 23, 36, 3, 384000, tzinfo=timezone(timedelta(hours=3)))
    catalog_entry = next(
        milestone
        for milestone in api.list_predefined_milestones()
        if milestone.id == "cdc-18-cognitive-copies-you-doing-chores"
    )

    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=client))
    monkeypatch.setattr(api, "_validate_milestone_time", AsyncMock(return_value=occurred_at))
    monkeypatch.setattr("huckleberry_api.api.time.time", lambda: 1_789_590_973.865)
    monkeypatch.setattr("huckleberry_api.api.secrets.token_hex", lambda _: "e72673bdb29703b7703a")
    created = await api.create_predefined_milestone(
        "child",
        milestone=catalog_entry,
        start_time=occurred_at,
        notes="  achieved  ",
    )

    assert isinstance(created.milestone, FirebasePredefinedMilestoneData)
    assert created.milestone.milestoneId == catalog_entry.id
    assert created.milestone.name == catalog_entry.title
    assert created.milestone.notes == "achieved"
    intervals = client.collection.return_value.document.return_value.collection.return_value
    intervals.where.assert_called_once()
    duplicate_query.get.assert_awaited_once_with()
    interval_document.set.assert_awaited_once_with(created.milestone.model_dump(exclude_none=True))


async def test_create_predefined_milestone_rejects_duplicate(websession, monkeypatch) -> None:
    """A catalog ID already logged for the child cannot be created twice."""
    api = HuckleberryAPI("test", "test", "UTC", websession)
    client, _, interval_document, duplicate_query = _milestone_write_client()
    duplicate_query.get = AsyncMock(return_value=[MagicMock()])
    catalog_entry = api.list_predefined_milestones()[0]

    monkeypatch.setattr(api, "_get_firestore_client", AsyncMock(return_value=client))
    monkeypatch.setattr(api, "_validate_milestone_time", AsyncMock(return_value=datetime.now(timezone.utc)))
    with pytest.raises(ValueError, match="already logged"):
        await api.create_predefined_milestone(
            "child",
            milestone=catalog_entry,
            start_time=datetime.now(timezone.utc),
        )

    interval_document.set.assert_not_awaited()


async def test_create_predefined_milestone_rejects_modified_catalog_entry(websession, monkeypatch) -> None:
    """Matching an ID alone is insufficient when other catalog metadata was changed."""
    api = HuckleberryAPI("test", "test", "UTC", websession)
    original = api.list_predefined_milestones()[0]
    modified = original.model_copy(update={"title": "Not the catalog title"})
    validate_time = AsyncMock()
    monkeypatch.setattr(api, "_validate_milestone_time", validate_time)

    with pytest.raises(ValueError, match="exactly match"):
        await api.create_predefined_milestone(
            "child",
            milestone=modified,
            start_time=datetime.now(timezone.utc),
        )

    validate_time.assert_not_awaited()


async def test_milestone_time_is_limited_to_birthdate_and_now(websession, monkeypatch) -> None:
    """Creation dates should match the app picker's allowed range."""
    api = HuckleberryAPI("test", "test", "UTC", websession)
    monkeypatch.setattr(
        api,
        "get_child",
        AsyncMock(return_value=FirebaseChildDocument(childsName="Test", birthdate="2026-01-10")),
    )

    with pytest.raises(ValueError, match="before"):
        await api._validate_milestone_time("child", datetime(2026, 1, 9, tzinfo=timezone.utc))

    with pytest.raises(ValueError, match="future"):
        await api._validate_milestone_time("child", datetime.now(timezone.utc) + timedelta(days=1))


async def test_list_available_predefined_milestones_excludes_logged_ids(websession, monkeypatch) -> None:
    """Availability should mirror the app's disabled state by catalog ID."""
    api = HuckleberryAPI("test", "test", "UTC", websession)
    logged = api.list_predefined_milestones()[0]
    record = MilestoneRecord(
        id="document",
        milestone=FirebasePredefinedMilestoneData(
            start=1.0,
            offset=0.0,
            name=logged.title,
            lastUpdated=2.0,
            milestoneId=logged.id,
            milestoneCategory=logged.category,
            milestoneAgeRange=logged.typical_window,
            milestoneSource=logged.source,
        ),
    )
    monkeypatch.setattr(api, "list_milestones", AsyncMock(return_value=[record]))

    available = await api.list_available_predefined_milestones("child")

    assert logged not in available
    assert len(available) == 82
