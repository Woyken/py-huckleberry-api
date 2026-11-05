"""Range queries over saved tracker history."""

from __future__ import annotations

from datetime import datetime
from google.api_core.exceptions import GoogleAPICallError
from google.cloud import firestore
from pydantic import TypeAdapter
from pydantic import ValidationError
from .client import HuckleberryBase
from .common import _LOGGER
from .firebase_types import FirebaseActivityIntervalData
from .firebase_types import FirebaseActivityMultiContainer
from .firebase_types import FirebaseDiaperData
from .firebase_types import FirebaseDiaperMultiContainer
from .firebase_types import FirebaseFeedIntervalData
from .firebase_types import FirebaseFeedMultiContainer
from .firebase_types import FirebaseHealthMultiContainer
from .firebase_types import FirebasePumpIntervalData
from .firebase_types import FirebasePumpMultiContainer
from .firebase_types import FirebaseSleepIntervalData
from .firebase_types import FirebaseSleepMultiContainer
from .firebase_types import HealthDataEntry


_FEED_INTERVAL_ADAPTER = TypeAdapter(FirebaseFeedIntervalData)

_HEALTH_ENTRY_ADAPTER = TypeAdapter(HealthDataEntry)


class HistoryMixin(HuckleberryBase):
    """Date-range history queries for every tracker."""

    async def list_sleep_intervals(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FirebaseSleepIntervalData]:
        """
        Fetch sleep intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated sleep interval entries
        """
        events: list[FirebaseSleepIntervalData] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        sleep_ref = client.collection("sleep").document(child_uid)
        intervals_ref = sleep_ref.collection("intervals")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = (
                intervals_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                interval = FirebaseSleepIntervalData.model_validate(data)
                events.append(interval)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = intervals_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebaseSleepMultiContainer.model_validate(data)

                # Iterate through batched entries and filter by date
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching sleep intervals: %s", err)

        return events

    async def list_feed_intervals(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FirebaseFeedIntervalData]:
        """
        Fetch feeding intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated feed interval entries
        """
        events: list[FirebaseFeedIntervalData] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        feed_ref = client.collection("feed").document(child_uid)
        intervals_ref = feed_ref.collection("intervals")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = (
                intervals_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                interval = _FEED_INTERVAL_ADAPTER.validate_python(data)
                feed_mode = getattr(interval, "mode", None)
                if feed_mode is None:
                    continue

                events.append(interval)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = intervals_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebaseFeedMultiContainer.model_validate(data)

                # Iterate through batched entries and filter by date
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    feed_mode = getattr(entry, "mode", None)
                    if feed_mode is None:
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching feed intervals: %s", err)

        return events

    async def list_diaper_intervals(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FirebaseDiaperData]:
        """
        Fetch diaper intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated diaper interval entries
        """
        events: list[FirebaseDiaperData] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        diaper_ref = client.collection("diaper").document(child_uid)
        intervals_ref = diaper_ref.collection("intervals")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = (
                intervals_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                entry = FirebaseDiaperData.model_validate(data)
                events.append(entry)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = intervals_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebaseDiaperMultiContainer.model_validate(data)

                # Iterate through batched entries and filter by date
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching diaper intervals: %s", err)

        return events

    async def list_health_entries(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[HealthDataEntry]:
        """
        Fetch health entries from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated health entries
        """
        events: list[HealthDataEntry] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        health_ref = client.collection("health").document(child_uid)
        # Health uses "data" subcollection, not "intervals"
        data_ref = health_ref.collection("data")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = (
                data_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                entry = _HEALTH_ENTRY_ADAPTER.validate_python(data)
                events.append(entry)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = data_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebaseHealthMultiContainer.model_validate(data)

                # Iterate through batched entries and filter by date
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching health entries: %s", err)

        return events

    async def list_pump_intervals(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FirebasePumpIntervalData]:
        """
        Fetch pump intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated pump interval entries
        """
        events: list[FirebasePumpIntervalData] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        pump_ref = client.collection("pump").document(child_uid)
        intervals_ref = pump_ref.collection("intervals")

        try:
            # Query 1: Get regular documents with date filtering
            regular_docs = (
                intervals_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue  # Skip multi-entry docs from this query

                entry = FirebasePumpIntervalData.model_validate(data)
                events.append(entry)

            # Query 2: Get multi-entry documents (can't filter by nested start field)
            multi_docs = intervals_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebasePumpMultiContainer.model_validate(data)

                # Iterate through batched entries and filter by date
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching pump intervals: %s", err)

        return events

    async def list_activity_intervals(
        self,
        child_uid: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[FirebaseActivityIntervalData]:
        """
        Fetch activity intervals from Firestore for a date range.

        Args:
            child_uid: Child unique identifier
            start_time: Start of range
            end_time: End of range

        Returns:
            List of Firebase-validated activity interval entries
        """
        events: list[FirebaseActivityIntervalData] = []
        start_timestamp, end_timestamp = start_time.timestamp(), end_time.timestamp()
        client = await self._get_firestore_client()
        activities_ref = client.collection("activities").document(child_uid)
        intervals_ref = activities_ref.collection("intervals")

        try:
            regular_docs = (
                intervals_ref.where(filter=firestore.FieldFilter("start", ">=", start_timestamp))
                .where(filter=firestore.FieldFilter("start", "<", end_timestamp))
                .order_by("start")
                .stream()
            )

            async for doc in regular_docs:
                data = doc.to_dict()
                if not data or data.get("multi"):
                    continue

                entry = FirebaseActivityIntervalData.model_validate(data)
                events.append(entry)

            multi_docs = intervals_ref.where(filter=firestore.FieldFilter("multi", "==", True)).stream()

            async for doc in multi_docs:
                data = doc.to_dict()
                if not data:
                    continue

                container = FirebaseActivityMultiContainer.model_validate(data)
                for entry in container.data.values():
                    entry_start = entry.start
                    if not (start_timestamp <= entry_start < end_timestamp):
                        continue

                    events.append(entry)

        except (GoogleAPICallError, ValidationError) as err:
            _LOGGER.error("Error fetching activity intervals: %s", err)

        return events
