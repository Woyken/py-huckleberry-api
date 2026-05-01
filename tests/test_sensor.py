"""Test Huckleberry sensors."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.huckleberry.const import DOMAIN
from huckleberry_api.firebase_types import (
    FirebaseChildDocument,
    FirebaseChildSweetspot,
    FirebaseDiaperDocumentData,
    FirebaseDiaperPrefs,
    FirebaseFeedDocumentData,
    FirebaseFeedPrefs,
    FirebaseGrowthData,
    FirebaseHealthDocumentData,
    FirebaseHealthPrefs,
    FirebaseLastBottleData,
    FirebaseLastDiaperData,
)


async def test_sensors(hass: HomeAssistant, mock_huckleberry_api):
    """Test sensors."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "test_password",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.huckleberry.HuckleberryAPI",
        return_value=mock_huckleberry_api,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Get the coordinator
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Simulate growth and diaper data via typed models
    state = coordinator._realtime_data["child_1"]
    state.health_status = FirebaseHealthDocumentData(
        prefs=FirebaseHealthPrefs(
            lastGrowthEntry=FirebaseGrowthData(
                mode="growth",
                start=1234567890,
                lastUpdated=1234567890,
                offset=0,
                weight=10.5,
                weightUnits="kg",
                height=75.0,
                heightUnits="cm",
                head=45.0,
                headUnits="hcm",
            ),
        ),
    )
    state.diaper_status = FirebaseDiaperDocumentData(
        prefs=FirebaseDiaperPrefs(
            lastDiaper=FirebaseLastDiaperData(
                mode="pee",
                start=1234567890,
                offset=0,
            ),
        ),
    )
    coordinator.async_set_updated_data(dict(coordinator._realtime_data))
    await hass.async_block_till_done()

    # Check children count sensor
    sensor_state = hass.states.get("sensor.huckleberry_children")
    assert sensor_state.state == "1"
    assert sensor_state.attributes["children"][0]["name"] == "Test Child"

    # Check child profile sensor
    sensor_state = hass.states.get("sensor.test_child_profile")
    assert sensor_state.state == "Test Child"
    assert sensor_state.attributes["birthday"] == "2023-01-01"

    # Check growth sensor
    sensor_state = hass.states.get("sensor.test_child_growth")
    expected_date = datetime.fromtimestamp(1234567890, tz=timezone.utc).isoformat()
    assert sensor_state.state == expected_date
    assert sensor_state.attributes["weight"] == 10.5
    assert sensor_state.attributes["height"] == 75.0

    # Check diaper sensor
    sensor_state = hass.states.get("sensor.test_child_diaper")
    assert sensor_state is not None
    assert sensor_state.state == expected_date
    assert sensor_state.attributes["type"] == "Pee"
    assert sensor_state.attributes["time"] == datetime.fromtimestamp(1234567890, tz=timezone.utc).isoformat()

async def test_bottle_sensor(hass: HomeAssistant, mock_huckleberry_api):
    """Test bottle sensor."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "test_password",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.huckleberry.HuckleberryAPI",
        return_value=mock_huckleberry_api,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Get the coordinator
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Simulate bottle data via typed models
    state = coordinator._realtime_data["child_1"]
    state.feed_status = FirebaseFeedDocumentData(
        prefs=FirebaseFeedPrefs(
            lastBottle=FirebaseLastBottleData(
                mode="bottle",
                start=1234567890,
                bottleAmount=120.0,
                bottleUnits="ml",
                bottleType="Formula",
                offset=0,
            ),
        ),
    )
    coordinator.async_set_updated_data(dict(coordinator._realtime_data))
    await hass.async_block_till_done()

    # Check bottle sensor
    sensor_state = hass.states.get("sensor.test_child_bottle")
    assert sensor_state is not None
    assert sensor_state.state == datetime.fromtimestamp(1234567890, tz=timezone.utc).isoformat()
    assert sensor_state.attributes["amount"] == 120.0
    assert sensor_state.attributes["units"] == "ml"
    assert sensor_state.attributes["type"] == "Formula"
    assert sensor_state.attributes["time"] == datetime.fromtimestamp(1234567890, tz=timezone.utc).isoformat()


async def test_entities_skip_blank_configuration_url(hass: HomeAssistant, mock_huckleberry_api):
    """Test entities are created when the child picture URL is blank."""
    mock_huckleberry_api.get_child.return_value = FirebaseChildDocument(
        childsName="Test Child",
        birthdate="2023-01-01",
        gender="M",
        picture="",
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "test_password",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.huckleberry.HuckleberryAPI",
        return_value=mock_huckleberry_api,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get("switch.test_child_sleep_timer") is not None
    assert hass.states.get("sensor.test_child_profile") is not None
    assert hass.states.get("calendar.test_child_events") is not None


async def test_sweetspot_sensor_state_and_attributes(
    hass: HomeAssistant, mock_huckleberry_api
):
    """Test enabled sweetspot sensor follows selected nap-day mode."""
    mock_huckleberry_api.get_child = AsyncMock(
        return_value=FirebaseChildDocument(
            childsName="Test Child",
            birthdate="2023-01-01",
            gender="M",
        )
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "test_password",
        },
    )
    entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "child_1_sweetspot",
        config_entry=entry,
        disabled_by=None,
        original_name="Sweetspot",
        suggested_object_id="test_child_sweetspot",
    )

    with patch(
        "custom_components.huckleberry.HuckleberryAPI",
        return_value=mock_huckleberry_api,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    future_zero = int((datetime.now(tz=timezone.utc) + timedelta(hours=3)).timestamp())
    future_one = int((datetime.now(tz=timezone.utc) + timedelta(hours=1)).timestamp())
    future_two = int((datetime.now(tz=timezone.utc) + timedelta(hours=2)).timestamp())

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coordinator._realtime_data["child_1"].child_document = FirebaseChildDocument(
        childsName="Test Child",
        birthdate="2023-01-01",
        gender="M",
        sweetspot=FirebaseChildSweetspot(
            selectedNapDay=0.0,
            sweetSpotTimes=[future_zero, future_two, future_one],
        ),
    )
    coordinator.async_set_updated_data(dict(coordinator._realtime_data))
    await hass.async_block_till_done()

    sensor_state = hass.states.get("sensor.test_child_sweetspot")
    assert sensor_state is not None
    assert sensor_state.state == datetime.fromtimestamp(future_zero, tz=timezone.utc).isoformat()
    assert sensor_state.attributes["selected_nap_day"] == 0
    assert sensor_state.attributes["0_nap_day_time"] == datetime.fromtimestamp(future_zero, tz=timezone.utc).isoformat()
    assert sensor_state.attributes["1_nap_day_time"] == datetime.fromtimestamp(future_two, tz=timezone.utc).isoformat()
    assert sensor_state.attributes["2_nap_day_time"] == datetime.fromtimestamp(future_one, tz=timezone.utc).isoformat()


async def test_sweetspot_sensor_unavailable_when_selected_time_missing(
    hass: HomeAssistant, mock_huckleberry_api
):
    """Test sweetspot sensor is unavailable when selectedNapDay cannot be resolved."""
    mock_huckleberry_api.get_child = AsyncMock(
        return_value=FirebaseChildDocument(
            childsName="Test Child",
            birthdate="2023-01-01",
            gender="M",
        )
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_EMAIL: "test@example.com",
            CONF_PASSWORD: "test_password",
        },
    )
    entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "child_1_sweetspot",
        config_entry=entry,
        disabled_by=None,
        original_name="Sweetspot",
        suggested_object_id="test_child_sweetspot",
    )

    with patch(
        "custom_components.huckleberry.HuckleberryAPI",
        return_value=mock_huckleberry_api,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    future_one = int((datetime.now(tz=timezone.utc) + timedelta(hours=1)).timestamp())

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coordinator._realtime_data["child_1"].child_document = FirebaseChildDocument(
        childsName="Test Child",
        birthdate="2023-01-01",
        gender="M",
        sweetspot=FirebaseChildSweetspot(
            selectedNapDay=3.0,
            sweetSpotTimes=[None, future_one, future_one],
        ),
    )
    coordinator.async_set_updated_data(dict(coordinator._realtime_data))
    await hass.async_block_till_done()

    sensor_state = hass.states.get("sensor.test_child_sweetspot")
    assert sensor_state is not None
    assert sensor_state.state == "unknown"
