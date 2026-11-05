"""Health tracker: growth, temperature, and medicine."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from google.api_core.exceptions import GoogleAPICallError
from pydantic import TypeAdapter
from pydantic import ValidationError
from typing import Literal
from .client import HuckleberryBase
from .common import _LOGGER
from .firebase_types import FirebaseGrowthData
from .firebase_types import FirebaseHealthDocumentData
from .firebase_types import FirebaseLastGrowthData
from .firebase_types import FirebaseLastMedicationData
from .firebase_types import FirebaseLastTemperatureData
from .firebase_types import FirebaseMedicationData
from .firebase_types import FirebaseMedicationTypeDocument
from .firebase_types import FirebaseMedicationTypeLastTake
from .firebase_types import FirebaseTemperatureData
from .firebase_types import MedicationUnits
from .firebase_types import TemperatureUnits
from .firebase_types import to_firebase_dict


_MEDICATION_UNITS_ADAPTER = TypeAdapter(MedicationUnits)


class HealthMixin(HuckleberryBase):
    """Growth, temperature, and medicine methods."""

    async def log_growth(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        weight: float | None = None,
        height: float | None = None,
        head: float | None = None,
        units: Literal["metric", "imperial"] = "metric",
    ) -> None:
        """
        Log growth measurements (weight, height, head circumference).

        Args:
            child_uid: Child unique identifier
            start_time: Event time
            weight: Weight measurement (kg for metric, lbs for imperial)
            height: Height measurement (cm for metric, inches for imperial)
            head: Head circumference (cm for metric, inches for imperial)
            units: 'metric' or 'imperial'
        """
        _LOGGER.info("Logging growth data for child %s", child_uid)

        if not any([weight, height, head]):
            raise ValueError("At least one measurement (weight, height, or head) is required")
        if units not in ("metric", "imperial"):
            raise ValueError("units must be 'metric' or 'imperial'")

        client = await self._get_firestore_client()
        health_ref = client.collection("health").document(child_uid)

        start_timestamp = start_time.timestamp()
        current_time = time.time()
        current_offset = await self._get_timezone_offset_minutes()
        health_doc = await health_ref.get()
        health_model = FirebaseHealthDocumentData.model_validate(health_doc.to_dict() or {})
        existing_last_growth = health_model.prefs.lastGrowthEntry if health_model.prefs else None
        existing_last_growth_start = existing_last_growth.start if existing_last_growth else None
        should_update_last_growth = existing_last_growth_start is None or start_timestamp >= float(
            existing_last_growth_start
        )

        # Create interval ID (timestamp in ms + random suffix)
        interval_timestamp_ms = int(current_time * 1000)
        interval_id = f"{interval_timestamp_ms}-{uuid.uuid4().hex[:20]}"

        # Build growth entry matching Huckleberry app structure
        growth_entry = FirebaseGrowthData(
            mode="growth",
            start=start_timestamp,
            lastUpdated=current_time,
            offset=current_offset,
        )

        # Add measurements with proper unit fields (matches app structure)
        if units == "metric":
            if weight is not None:
                growth_entry.weight = float(weight)
                growth_entry.weightUnits = "kg"
            if height is not None:
                growth_entry.height = float(height)
                growth_entry.heightUnits = "cm"
            if head is not None:
                growth_entry.head = float(head)
                growth_entry.headUnits = "hcm"
        else:  # imperial
            if weight is not None:
                growth_entry.weight = float(weight)
                growth_entry.weightUnits = "lbs.oz"
            if height is not None:
                growth_entry.height = float(height)
                growth_entry.heightUnits = "ft.in"
            if head is not None:
                growth_entry.head = float(head)
                growth_entry.headUnits = "hin"

        last_growth_entry = FirebaseLastGrowthData(
            **growth_entry.model_dump(),
            _id=interval_id,
            type="health",
            isNight=False,
            multientry_key=None,
        )

        # Create interval document in health/{child_uid}/data subcollection
        # (Health uses "data" subcollection, not "intervals" like other trackers)
        health_data_ref = health_ref.collection("data").document(interval_id)

        await health_data_ref.set(to_firebase_dict(growth_entry))
        _LOGGER.info("Created growth data entry in subcollection: %s", interval_id)

        # Update prefs.lastGrowthEntry and timestamps (matches Huckleberry app structure)
        if should_update_last_growth:
            last_growth_payload = to_firebase_dict(last_growth_entry)
            last_growth_payload["multientry_key"] = None
            try:
                await health_ref.update(
                    {
                        "prefs.lastGrowthEntry": last_growth_payload,
                        "prefs.timestamp": {"seconds": current_time},
                        "prefs.local_timestamp": current_time,
                    }
                )
            except GoogleAPICallError as err:
                _LOGGER.error("Failed to log growth data: %s", err)
                raise

        _LOGGER.info("Growth data logged successfully (updated_last=%s)", should_update_last_growth)

    async def log_temperature(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        amount: float,
        units: TemperatureUnits,
        notes: str | None = None,
    ) -> None:
        """Log a body-temperature measurement.

        Args:
            child_uid: Child unique identifier
            start_time: Measurement time
            amount: Temperature value
            units: ``C`` for Celsius or ``F`` for Fahrenheit
            notes: Optional notes attached to the measurement
        """
        _LOGGER.info("Logging temperature data for child %s", child_uid)

        client = await self._get_firestore_client()
        health_ref = client.collection("health").document(child_uid)

        start_timestamp = start_time.timestamp()
        current_time = time.time()
        current_offset = await self._get_timezone_offset_minutes()
        health_doc = await health_ref.get()
        health_model = FirebaseHealthDocumentData.model_validate(health_doc.to_dict() or {})
        existing_last_temperature = health_model.prefs.lastTemperature if health_model.prefs else None
        existing_last_temperature_start = existing_last_temperature.start if existing_last_temperature else None
        should_update_last_temperature = existing_last_temperature_start is None or start_timestamp >= float(
            existing_last_temperature_start
        )

        interval_timestamp_ms = int(current_time * 1000)
        interval_id = f"{interval_timestamp_ms}-{uuid.uuid4().hex[:20]}"
        temperature_entry = FirebaseTemperatureData(
            mode="temperature",
            start=start_timestamp,
            lastUpdated=current_time,
            offset=current_offset,
            amount=float(amount),
            units=units,
            notes=notes,
        )
        last_temperature = FirebaseLastTemperatureData(
            _id=interval_id,
            type="health",
            mode="temperature",
            start=start_timestamp,
            lastUpdated=current_time,
            offset=current_offset,
            amount=float(amount),
            units=units,
            multientry_key=None,
        )

        health_data_ref = health_ref.collection("data").document(interval_id)
        await health_data_ref.set(to_firebase_dict(temperature_entry))
        _LOGGER.info("Created temperature data entry in subcollection: %s", interval_id)

        if should_update_last_temperature:
            try:
                await health_ref.update(
                    {
                        "prefs.lastTemperature": last_temperature.model_dump(by_alias=True),
                        "prefs.timestamp": {"seconds": current_time},
                        "prefs.local_timestamp": current_time,
                    }
                )
            except GoogleAPICallError as err:
                _LOGGER.error("Failed to log temperature data: %s", err)
                raise

        _LOGGER.info("Temperature data logged successfully (updated_last=%s)", should_update_last_temperature)

    async def list_medicine_types(self, child_uid: str) -> list[FirebaseMedicationTypeDocument]:
        """List child-specific medicine types from ``health/{child_uid}/types``."""
        client = await self._get_firestore_client()
        types_ref = client.collection("health").document(child_uid).collection("types")

        medicine_types: list[FirebaseMedicationTypeDocument] = []
        async for doc in types_ref.stream():
            item = FirebaseMedicationTypeDocument.model_validate(doc.to_dict() or {})
            if item.active:
                medicine_types.append(item)

        return sorted(medicine_types, key=lambda item: item.name.casefold())

    async def create_medicine_type(self, child_uid: str, name: str) -> FirebaseMedicationTypeDocument:
        """Create a selectable medicine type for a child."""
        medicine_name = name.strip()
        if not medicine_name:
            raise ValueError("Medicine name must be non-empty")

        current_time = time.time()
        medicine_id = f"{int(current_time * 1000)}-{uuid.uuid4().hex[:20]}"
        medicine_type = FirebaseMedicationTypeDocument(
            _id=medicine_id,
            active=True,
            mode="medication",
            name=medicine_name,
        )

        client = await self._get_firestore_client()
        type_ref = client.collection("health").document(child_uid).collection("types").document(medicine_id)
        await type_ref.set(to_firebase_dict(medicine_type))
        return medicine_type

    async def log_medicine(
        self,
        child_uid: str,
        *,
        start_time: datetime,
        medicine_type: FirebaseMedicationTypeDocument,
        amount: float | None = None,
        units: MedicationUnits | None = None,
        notes: str = "",
    ) -> None:
        """Log medicine using an existing child-specific medicine type."""
        medicine_id = medicine_type.id_.strip()
        medicine_name = medicine_type.name.strip()
        if not medicine_id:
            raise ValueError("Medicine type ID must be non-empty")
        if not medicine_name:
            raise ValueError("Medicine name must be non-empty")
        if units is not None:
            units = _MEDICATION_UNITS_ADAPTER.validate_python(units)
        if amount is not None and units is None:
            raise ValueError("Medicine units are required when an amount is provided")

        client = await self._get_firestore_client()
        health_ref = client.collection("health").document(child_uid)
        start_timestamp = start_time.timestamp()
        current_time = time.time()
        current_offset = await self._get_timezone_offset_minutes()
        interval_id = f"{int(current_time * 1000)}-{uuid.uuid4().hex[:20]}"

        health_doc = await health_ref.get()
        health_model = FirebaseHealthDocumentData.model_validate(health_doc.to_dict() or {})
        existing_last_medicine = health_model.prefs.lastMedication if health_model.prefs else None
        existing_last_medicine_start = existing_last_medicine.start if existing_last_medicine else None
        should_update_last_medicine = existing_last_medicine_start is None or start_timestamp >= float(
            existing_last_medicine_start
        )
        latest_units = units if units is not None else existing_last_medicine.units if existing_last_medicine else None
        logged_amount = 0.0 if amount is None else float(amount)

        medicine_entry = FirebaseMedicationData(
            mode="medication",
            start=start_timestamp,
            lastUpdated=current_time,
            offset=current_offset,
            medication_id=medicine_id,
            medication_name=medicine_name,
            amount=logged_amount,
            units=units,
            notes=notes,
        )
        last_medicine = FirebaseLastMedicationData(
            **medicine_entry.model_dump(exclude={"units"}),
            _id=interval_id,
            type="health",
            isNight=False,
            multientry_key=None,
            units=latest_units,
        )
        last_take = FirebaseMedicationTypeLastTake(
            amount=float(amount) if amount is not None else None,
            reminderType="at",
            units=units,
        )

        await health_ref.collection("data").document(interval_id).set(to_firebase_dict(medicine_entry))
        await (
            health_ref.collection("types")
            .document(medicine_id)
            .set(
                {"lastTake": to_firebase_dict(last_take)},
                merge=True,
            )
        )

        if should_update_last_medicine:
            last_medicine_payload = to_firebase_dict(last_medicine)
            last_medicine_payload["multientry_key"] = None
            await health_ref.update(
                {
                    "prefs.lastMedication": last_medicine_payload,
                    "prefs.timestamp": {"seconds": current_time},
                    "prefs.local_timestamp": current_time,
                }
            )

    async def get_latest_growth(self, child_uid: str) -> FirebaseGrowthData | None:
        """
        Get the latest growth measurements for a child.

        Args:
            child_uid: Child unique identifier

        Returns:
            Latest Firebase growth entry, if present
        """
        client = await self._get_firestore_client()
        health_ref = client.collection("health").document(child_uid)

        try:
            doc = await health_ref.get()
            if not doc.exists:
                return None

            health_data = doc.to_dict()
            if not health_data:
                return None

            health_model = FirebaseHealthDocumentData.model_validate(health_data)
            last_growth = health_model.prefs.lastGrowthEntry if health_model.prefs else None

            if not last_growth:
                return None

            return last_growth
        except (GoogleAPICallError, ValidationError, RuntimeError, TypeError, ValueError) as err:
            _LOGGER.error("Failed to get growth data: %s", err)
            return None
