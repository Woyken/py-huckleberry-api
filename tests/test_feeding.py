"""Feeding tracking tests for Huckleberry API."""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from google.cloud import firestore

from huckleberry_api import HuckleberryAPI


class TestFeedingTracking:
    """Test feeding tracking functionality."""

    async def test_start_and_cancel_feeding(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test starting and canceling feeding."""
        # Start nursing
        await api.start_nursing(child_uid, side="left")
        await asyncio.sleep(1)

        db = await api._get_firestore_client()

        feed_doc = await db.collection("feed").document(child_uid).get()
        assert feed_doc.exists
        data = feed_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is True
        assert data["timer"]["activeSide"] == "left"

        # Cancel nursing
        await api.cancel_nursing(child_uid)
        await asyncio.sleep(1)

        feed_doc = await db.collection("feed").document(child_uid).get()
        data = feed_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is False

    async def test_feeding_with_side_switch(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test feeding with side switching."""
        # Start nursing on left
        await api.start_nursing(child_uid, side="left")
        await asyncio.sleep(2)

        # Switch to right
        await api.switch_nursing_side(child_uid)
        await asyncio.sleep(2)

        db = await api._get_firestore_client()

        feed_doc = await db.collection("feed").document(child_uid).get()
        data = feed_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is True
        assert data["timer"]["activeSide"] == "right"
        assert data["timer"]["leftDuration"] > 0

        # Complete nursing
        await api.complete_nursing(child_uid)
        await asyncio.sleep(1)

        feed_doc = await db.collection("feed").document(child_uid).get()
        data = feed_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is False
        assert "lastNursing" in data.get("prefs", {})

    async def test_feeding_pause_resume(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test feeding pause and resume."""
        # Start nursing
        await api.start_nursing(child_uid, side="right")
        await asyncio.sleep(2)

        # Pause
        await api.pause_nursing(child_uid)
        await asyncio.sleep(1)

        db = await api._get_firestore_client()

        feed_doc = await db.collection("feed").document(child_uid).get()
        data = feed_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is True
        assert data["timer"]["paused"] is True

        # Resume
        await api.resume_nursing(child_uid)
        await asyncio.sleep(1)

        feed_doc = await db.collection("feed").document(child_uid).get()
        data = feed_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is True
        assert data["timer"]["paused"] is False

        # Cancel to cleanup
        await api.cancel_nursing(child_uid)

    async def test_resume_feeding_with_explicit_side(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test resuming feeding with explicit side parameter."""
        # Start nursing on left
        await api.start_nursing(child_uid, side="left")
        await asyncio.sleep(2)

        # Pause
        await api.pause_nursing(child_uid)
        await asyncio.sleep(1)

        # Resume on right (explicit side)
        await api.resume_nursing(child_uid, side="right")
        await asyncio.sleep(1)

        db = await api._get_firestore_client()

        feed_doc = await db.collection("feed").document(child_uid).get()
        data = feed_doc.to_dict()
        assert data is not None
        assert data["timer"]["active"] is True
        assert data["timer"]["paused"] is False
        assert data["timer"]["activeSide"] == "right"

        # Cancel to cleanup
        await api.cancel_nursing(child_uid)

    async def test_complete_feeding_creates_interval(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that completing feeding creates interval document."""
        # Start and complete nursing
        created_after = time.time()
        await api.start_nursing(child_uid, side="left")
        await asyncio.sleep(3)
        await api.complete_nursing(child_uid)
        await asyncio.sleep(2)

        db = await api._get_firestore_client()

        # Check intervals subcollection
        intervals_ref = db.collection("feed").document(child_uid).collection("intervals")

        recent_intervals = intervals_ref.order_by("lastUpdated", direction=firestore.Query.DESCENDING).limit(10)

        intervals_list = list(await recent_intervals.get())
        assert len(intervals_list) > 0

        interval_data = next(
            (
                data
                for doc in intervals_list
                if (data := doc.to_dict())
                and data.get("mode") == "breast"
                and float(data.get("lastUpdated", 0.0)) >= created_after
            ),
            None,
        )
        assert interval_data is not None
        assert "start" in interval_data
        assert "mode" in interval_data
        assert interval_data["mode"] == "breast"

    async def test_complete_nursing_normalizes_ms_feed_start_time(
        self, api: HuckleberryAPI, child_uid: str
    ) -> None:
        """App-started sessions store timer.feedStartTime in milliseconds; the
        interval written on completion must still use epoch seconds.

        Regression test for issue #51: the resulting future-dated interval
        (year ~58000) freezes the official app's summary pages.
        """
        await api.start_nursing(child_uid, side="right")
        await asyncio.sleep(2)

        db = await api._get_firestore_client()
        feed_ref = db.collection("feed").document(child_uid)

        # Simulate the official iOS app having started this session: observed
        # live (2026-07), the app writes feedStartTime in milliseconds.
        start_ms = (time.time() - 60) * 1000
        await feed_ref.update({"timer.feedStartTime": start_ms})

        created_after = time.time()
        await api.complete_nursing(child_uid)
        await asyncio.sleep(2)

        intervals_ref = feed_ref.collection("intervals")
        recent = intervals_ref.order_by("lastUpdated", direction=firestore.Query.DESCENDING).limit(5)
        interval_data = next(
            (
                data
                for doc in await recent.get()
                if (data := doc.to_dict())
                and data.get("mode") == "breast"
                and float(data.get("lastUpdated", 0.0)) >= created_after
            ),
            None,
        )
        assert interval_data is not None
        assert interval_data["start"] == pytest.approx(start_ms / 1000, abs=1.0)

        prefs = (await feed_ref.get()).to_dict()["prefs"]
        assert prefs["lastNursing"]["start"] == pytest.approx(start_ms / 1000, abs=1.0)
        assert prefs["lastSide"]["start"] == pytest.approx(start_ms / 1000, abs=1.0)

    async def test_log_nursing_with_explicit_times(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging a completed nursing interval with explicit timestamps."""
        end_time = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=3)
        start_time = end_time - timedelta(minutes=40)

        await api.log_nursing(child_uid, start_time=start_time, end_time=end_time, side="right")
        await asyncio.sleep(1)

        db = await api._get_firestore_client()
        intervals_ref = db.collection("feed").document(child_uid).collection("intervals")
        matching = list(
            await intervals_ref.where(filter=firestore.FieldFilter("start", "==", start_time.timestamp())).get()
        )

        breast_entries = [doc.to_dict() for doc in matching if (doc.to_dict() or {}).get("mode") == "breast"]
        assert breast_entries
        interval_data = breast_entries[0]
        assert interval_data is not None
        assert interval_data["lastSide"] == "right"
        assert interval_data["rightDuration"] == (end_time - start_time).total_seconds()
