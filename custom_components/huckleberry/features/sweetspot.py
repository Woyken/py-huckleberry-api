"""Sweetspot-related entities for Huckleberry."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from huckleberry_api.firebase_types import FirebaseChildSweetspot

from .. import HuckleberryDataUpdateCoordinator
from ..entity import HuckleberryBaseEntity
from ..models import HuckleberryChildProfile
from ..timestamps import as_datetime


def build_sweetspot_sensors(
    coordinator: HuckleberryDataUpdateCoordinator,
    children: list[HuckleberryChildProfile],
) -> list[SensorEntity]:
    """Build sweetspot sensors."""
    return [HuckleberrySweetspotSensor(coordinator, child) for child in children]


def _selected_sweetspot_time(sweetspot: FirebaseChildSweetspot | None) -> datetime | None:
    """Return the sweetspot time for the selected nap-day mode."""
    if sweetspot is None or not sweetspot.sweetSpotTimes:
        return None

    selected_nap_day = sweetspot.selectedNapDay
    if selected_nap_day is None:
        return None

    selected_nap_day = int(float(selected_nap_day))
    if selected_nap_day >= len(sweetspot.sweetSpotTimes):
        return None
    selected_time = sweetspot.sweetSpotTimes[selected_nap_day]
    if selected_time is None:
        return None

    return as_datetime(selected_time)


class HuckleberrySweetspotSensor(HuckleberryBaseEntity, SensorEntity):
    """Sensor showing the next predicted sweetspot nap time."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "sweetspot"

    def __init__(self, coordinator: HuckleberryDataUpdateCoordinator, child: HuckleberryChildProfile) -> None:
        super().__init__(coordinator, child)
        self._attr_unique_id = f"{self.child_uid}_sweetspot"

    @property
    def _sweetspot(self) -> FirebaseChildSweetspot | None:
        """Return sweetspot data from the child document."""
        child_doc = self.coordinator.get_child_document(self.child_uid)
        if child_doc is not None:
            return child_doc.sweetspot
        # Fall back to initial profile document
        return self._child.document.sweetspot

    @property
    def native_value(self) -> datetime | None:
        """Return the selected sweetspot time as a datetime."""
        return _selected_sweetspot_time(self._sweetspot)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return factual sweetspot metadata and predicted times."""
        sweetspot = self._sweetspot
        if sweetspot is None:
            return {}

        attributes: dict[str, object] = {}

        if sweetspot.selectedNapDay is not None:
            attributes["selected_nap_day"] = sweetspot.selectedNapDay

        if sweetspot.sweetSpotTimes:
            for idx, value in enumerate(sweetspot.sweetSpotTimes):
                if value is None:
                    continue
                dt = as_datetime(value)
                if dt is not None:
                    attributes[f"{idx}_nap_day_time"] = dt.isoformat()

        return attributes
