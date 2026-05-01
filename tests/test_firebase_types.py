"""Unit tests for strict Firebase schema models."""

from huckleberry_api.firebase_types import (
    FirebaseActivityDocumentData,
    FirebaseActivityIntervalData,
    FirebaseActivityMultiContainer,
    FirebaseActivityPrefs,
    FirebaseActivityTimerData,
    FirebaseActivityTimerEntryData,
    FirebaseChildDocument,
    FirebaseChildSweetspot,
    FirebaseDiaperDocumentData,
    FirebaseFeedDocumentData,
    FirebaseGrowthData,
    FirebaseLastActivityData,
    FirebaseLastPumpData,
    FirebaseMedicationData,
    FirebasePumpDocumentData,
    FirebasePumpIntervalData,
    FirebasePumpMultiContainer,
    FirebasePumpPrefs,
    FirebaseSleepDocumentData,
)


def test_feed_document_accepts_empty_last_summary_maps() -> None:
    """Empty feed summary maps should validate after history is cleared."""
    model = FirebaseFeedDocumentData.model_validate(
        {
            "prefs": {
                "lastBottle": {},
                "lastNursing": {},
                "lastSolid": {},
            }
        }
    )

    assert model.prefs is not None
    assert model.prefs.lastBottle is not None
    assert model.prefs.lastBottle.mode is None
    assert model.prefs.lastBottle.bottleType is None
    assert model.prefs.lastNursing is not None
    assert model.prefs.lastNursing.mode is None
    assert model.prefs.lastNursing.duration is None
    assert model.prefs.lastSolid is not None
    assert model.prefs.lastSolid.mode is None
    assert model.prefs.lastSolid.foods is None


def test_sleep_and_diaper_documents_accept_empty_last_summary_maps() -> None:
    """Empty sleep and diaper summary maps should validate after deletions."""
    sleep_model = FirebaseSleepDocumentData.model_validate({"prefs": {"lastSleep": {}}})
    assert sleep_model.prefs is not None
    assert sleep_model.prefs.lastSleep is not None
    assert sleep_model.prefs.lastSleep.start is None
    assert sleep_model.prefs.lastSleep.duration is None

    diaper_model = FirebaseDiaperDocumentData.model_validate(
        {
            "prefs": {
                "lastDiaper": {},
                "lastPotty": {},
            }
        }
    )
    assert diaper_model.prefs is not None
    assert diaper_model.prefs.lastDiaper is not None
    assert diaper_model.prefs.lastDiaper.mode is None
    assert diaper_model.prefs.lastDiaper.start is None
    assert diaper_model.prefs.lastPotty is not None
    assert diaper_model.prefs.lastPotty.mode is None


def test_growth_model_accepts_live_app_imperial_summary_units() -> None:
    """Growth schema should accept the composite imperial units emitted by the live app."""
    model = FirebaseGrowthData.model_validate(
        {
            "_id": "1773175568582-ef0c64260d2686001e96",
            "head": 10.2,
            "headUnits": "hin",
            "height": 5.333333333333333,
            "heightUnits": "ft.in",
            "lastUpdated": 1773175568.582,
            "mode": "growth",
            "multientry_key": None,
            "offset": -120.0,
            "start": 1773175490.0,
            "type": "health",
            "weight": 14.125,
            "weightUnits": "lbs.oz",
        }
    )

    assert model.weightUnits == "lbs.oz"
    assert model.heightUnits == "ft.in"
    assert model.headUnits == "hin"


def test_growth_model_accepts_sparse_live_app_data_rows() -> None:
    """Growth data rows from the live app can omit summary-only fields like `_id` and `type`."""
    model = FirebaseGrowthData.model_validate(
        {
            "head": 30.9,
            "headUnits": "hcm",
            "height": 162.0,
            "heightUnits": "cm",
            "lastUpdated": 1773175665.799,
            "mode": "growth",
            "offset": -120.0,
            "start": 1773175645.668,
            "weight": 9.41,
            "weightUnits": "kg",
        }
    )

    assert model.id_ is None
    assert model.type is None
    assert model.isNight is None
    assert model.weightUnits == "kg"
    assert model.heightUnits == "cm"


def test_medication_model_accepts_live_app_ounce_units() -> None:
    """Medication schema should accept the live app's oz unit option."""
    model = FirebaseMedicationData.model_validate(
        {
            "type": "health",
            "mode": "medication",
            "start": 1773641000.0,
            "lastUpdated": 1773641001.0,
            "offset": 0.0,
            "medication_id": "abc123",
            "medication_name": "Vitamin D",
            "amount": 2.0,
            "units": "oz",
        }
    )

    assert model.units == "oz"


def test_pump_interval_model() -> None:
    """Test pump interval data model with leftright mode."""
    model = FirebasePumpIntervalData.model_validate(
        {
            "start": 1773175490.0,
            "entryMode": "leftright",
            "leftAmount": 1.5,
            "rightAmount": 1.6,
            "units": "oz",
            "offset": 420.0,
            "duration": 1800.0,
            "lastUpdated": 1773175490.0,
        }
    )

    assert model.start == 1773175490.0
    assert model.entryMode == "leftright"
    assert model.leftAmount == 1.5
    assert model.rightAmount == 1.6
    assert model.units == "oz"
    assert model.offset == 420.0
    assert model.duration == 1800.0
    assert model.lastUpdated == 1773175490.0


def test_pump_interval_model_total_mode() -> None:
    """Test pump interval data model with total mode."""
    model = FirebasePumpIntervalData.model_validate(
        {
            "start": 1773175490.0,
            "entryMode": "total",
            "leftAmount": 1.55,
            "rightAmount": 1.55,
            "units": "oz",
            "offset": 420.0,
            "duration": 1500.0,
        }
    )

    assert model.entryMode == "total"
    assert model.leftAmount == 1.55
    assert model.rightAmount == 1.55
    assert model.units == "oz"
    assert model.offset == 420.0
    assert model.duration == 1500.0


def test_pump_interval_model_ml_units() -> None:
    """Test pump interval data model with ml units."""
    model = FirebasePumpIntervalData.model_validate(
        {
            "start": 1773175490.0,
            "entryMode": "leftright",
            "leftAmount": 45.0,
            "rightAmount": 50.0,
            "units": "ml",
            "offset": 0.0,
        }
    )

    assert model.leftAmount == 45.0
    assert model.rightAmount == 50.0
    assert model.units == "ml"


def test_last_pump_data_model() -> None:
    """Test last pump data model for prefs.lastPump structure."""
    model = FirebaseLastPumpData.model_validate(
        {
            "start": 1773175490.0,
            "entryMode": "total",
            "leftAmount": 1.6,
            "rightAmount": 1.6,
            "units": "oz",
            "duration": 1800.0,
            "offset": 420.0,
        }
    )

    assert model.start == 1773175490.0
    assert model.entryMode == "total"
    assert model.leftAmount == 1.6
    assert model.rightAmount == 1.6
    assert model.units == "oz"
    assert model.duration == 1800.0
    assert model.offset == 420.0


def test_pump_prefs_model() -> None:
    """Test pump preferences model."""
    model = FirebasePumpPrefs.model_validate(
        {
            "lastPump": {
                "start": 1773175490.0,
                "entryMode": "leftright",
                "leftAmount": 1.5,
                "rightAmount": 1.6,
                "units": "oz",
                "duration": 1800.0,
                "offset": 420.0,
            },
            "timestamp": {"seconds": 1773175490, "nanos": 0},
        }
    )

    assert model.lastPump is not None
    assert model.lastPump.entryMode == "leftright"
    assert model.lastPump.units == "oz"


def test_pump_document_data_model() -> None:
    """Test pump document data model."""
    model = FirebasePumpDocumentData.model_validate(
        {
            "prefs": {
                "lastPump": {
                    "start": 1773175490.0,
                    "entryMode": "total",
                    "leftAmount": 2.0,
                    "rightAmount": 2.0,
                    "units": "oz",
                    "duration": 2000.0,
                    "offset": 420.0,
                }
            }
        }
    )

    assert model.prefs is not None
    assert model.prefs.lastPump is not None
    assert model.prefs.lastPump.entryMode == "total"
    assert model.prefs.lastPump.units == "oz"


def test_pump_multi_container_model() -> None:
    """Test pump multi-container model for batched writes."""
    model = FirebasePumpMultiContainer.model_validate(
        {
            "multi": True,
            "hasMoreRoom": False,
            "data": {
                "interval1": {
                    "start": 1773175490.0,
                    "entryMode": "leftright",
                    "leftAmount": 1.5,
                    "rightAmount": 1.6,
                    "units": "oz",
                    "offset": 420.0,
                },
                "interval2": {
                    "start": 1773176490.0,
                    "entryMode": "total",
                    "leftAmount": 1.55,
                    "rightAmount": 1.55,
                    "units": "oz",
                    "offset": 420.0,
                },
            },
        }
    )

    assert model.multi is True
    assert model.data is not None
    assert len(model.data) == 2
    assert "interval1" in model.data
    assert "interval2" in model.data
    assert model.data["interval2"].leftAmount == 1.55
    assert model.data["interval2"].rightAmount == 1.55


def test_activity_interval_model_with_notes() -> None:
    """Activity interval schema should accept verified live notes payloads."""
    model = FirebaseActivityIntervalData.model_validate(
        {
            "mode": "bath",
            "start": 1773638859.763,
            "offset": -120.0,
            "duration": 1020.0,
            "end_offset": -120.0,
            "lastUpdated": 1773638865.955,
            "notes": "j",
        }
    )

    assert model.mode == "bath"
    assert model.duration == 1020.0
    assert model.notes == "j"


def test_last_activity_data_model() -> None:
    """Activity prefs summary schema should match verified live payloads."""
    model = FirebaseLastActivityData.model_validate(
        {
            "start": 1773638810.595,
            "offset": -120.0,
            "duration": 1200.0,
            "end_offset": -120.0,
        }
    )

    assert model.start == 1773638810.595
    assert model.offset == -120.0
    assert model.duration == 1200.0
    assert model.end_offset == -120.0


def test_activity_timer_entry_model() -> None:
    """Per-mode activity timer schema should accept verified live timer payloads."""
    model = FirebaseActivityTimerEntryData.model_validate(
        {
            "active": True,
            "paused": False,
            "timestamp": {"seconds": 1773638888.092},
            "local_timestamp": 1773638888.092,
            "startTime": 1773638888090.0,
            "endTime": 1772998750620.0,
            "duration": 0.0,
            "notes": "",
            "uuid": "dca86f2ba764cf06",
        }
    )

    assert model.active is True
    assert model.endTime == 1772998750620.0
    assert model.uuid == "dca86f2ba764cf06"


def test_activity_prefs_and_document_model() -> None:
    """Activity root document schema should validate verified prefs and timer maps."""
    prefs = FirebaseActivityPrefs.model_validate(
        {
            "lastBath": {
                "start": 1773638859.763,
                "offset": -120.0,
                "duration": 1020.0,
                "end_offset": -120.0,
            },
            "lastStoryTime": {
                "start": 1773638797.005,
                "offset": -120.0,
            },
            "timestamp": {"seconds": 1773638865.953},
            "local_timestamp": 1773638865.953,
        }
    )
    timer = FirebaseActivityTimerData.model_validate(
        {
            "bath": {
                "active": True,
                "paused": False,
                "timestamp": {"seconds": 1773638888.092},
                "local_timestamp": 1773638888.092,
                "startTime": 1773638888090.0,
                "endTime": 1772998750620.0,
                "duration": 0.0,
                "notes": "",
                "uuid": "dca86f2ba764cf06",
            },
            "storyTime": {
                "active": False,
                "paused": False,
                "timestamp": {"seconds": 1773638800.647},
                "local_timestamp": 1773638800.647,
                "startTime": 1773638800647.0,
                "duration": 0.0,
                "notes": "",
                "uuid": "dca86f2ba764cf06",
            },
        }
    )
    document = FirebaseActivityDocumentData.model_validate(
        {
            "prefs": prefs.model_dump(by_alias=True, exclude_none=True),
            "timer": timer.model_dump(by_alias=True, exclude_none=True),
        }
    )

    assert document.prefs is not None
    assert document.prefs.lastBath is not None
    assert document.prefs.lastBath.duration == 1020.0
    assert document.timer is not None
    assert document.timer.bath is not None
    assert document.timer.bath.active is True
    assert document.timer.storyTime is not None
    assert document.timer.storyTime.active is False


def test_activity_multi_container_model() -> None:
    """Activity multi-container schema should validate batched interval docs."""
    model = FirebaseActivityMultiContainer.model_validate(
        {
            "multi": True,
            "hasMoreRoom": False,
            "data": {
                "interval1": {
                    "mode": "bath",
                    "start": 1773638859.763,
                    "offset": -120.0,
                    "duration": 1020.0,
                    "end_offset": -120.0,
                },
                "interval2": {
                    "mode": "storyTime",
                    "start": 1773638797.005,
                    "offset": -120.0,
                },
            },
        }
    )

    assert model.multi is True
    assert len(model.data) == 2
    assert model.data["interval1"].mode == "bath"
    assert model.data["interval2"].mode == "storyTime"


def test_child_document_accepts_list_shaped_sweetspot_times() -> None:
    """sweetSpotTimes is a list (not dict) with None placeholders from Firebase."""
    model = FirebaseChildDocument.model_validate(
        {
            "childsName": "Test Child",
            "birthdate": "2023-01-01",
            "gender": "M",
            "sweetspot": {
                "selectedNapDay": 3,
                "sweetSpotTimes": [None, None, None, 1777506600.0, 1777504800.0],
            },
        }
    )

    assert model.sweetspot is not None
    assert model.sweetspot.sweetSpotTimes == [None, None, None, 1777506600.0, 1777504800.0]
    assert model.sweetspot.selectedNapDay == 3


def test_child_document_normalizes_dict_shaped_sweetspot_times() -> None:
    """sweetSpotTimes may also arrive as a sparse dict from Firebase."""
    model = FirebaseChildDocument.model_validate(
        {
            "childsName": "Test Child",
            "birthdate": "2023-01-01",
            "gender": "M",
            "sweetspot": {
                "selectedNapDay": 4,
                "sweetSpotTimes": {"3": 1777567800.0, "4": 1777566600.0},
            },
        }
    )

    assert model.sweetspot is not None
    assert model.sweetspot.sweetSpotTimes == [None, None, None, 1777567800.0, 1777566600.0]
    assert model.sweetspot.selectedNapDay == 4
