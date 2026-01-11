"""Bottle feeding tests for Huckleberry API."""
import time

from google.cloud import firestore

from huckleberry_api import HuckleberryAPI


class TestBottleFeeding:
    """Test bottle feeding functionality."""

    def test_log_bottle_feeding_formula(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging formula bottle feeding."""
        # Log formula bottle
        api.log_bottle_feeding(child_uid, amount=120.0, bottle_type="Formula", units="ml")
        time.sleep(2)

        # Check interval created
        intervals_ref = (
            api._get_firestore_client()
            .collection("feed")
            .document(child_uid)
            .collection("intervals")
        )

        # Get most recent interval
        recent_intervals = (
            intervals_ref
            .order_by("start", direction=firestore.Query.DESCENDING)
            .limit(1)
            .get()
        )

        intervals_list = list(recent_intervals)
        assert len(intervals_list) > 0

        interval_data = intervals_list[0].to_dict()
        assert interval_data is not None
        assert interval_data["mode"] == "bottle"
        assert interval_data["bottleType"] == "Formula"
        assert interval_data["amount"] == 120.0
        assert interval_data["units"] == "ml"
        assert "start" in interval_data
        assert "lastUpdated" in interval_data
        assert "offset" in interval_data

        # Check prefs.lastBottle updated
        feed_doc = api._get_firestore_client().collection("feed").document(child_uid).get()
        data = feed_doc.to_dict()
        assert data is not None
        prefs = data.get("prefs", {})
        assert "lastBottle" in prefs
        assert prefs["lastBottle"]["mode"] == "bottle"
        assert prefs["lastBottle"]["bottleType"] == "Formula"
        assert prefs["lastBottle"]["bottleAmount"] == 120.0
        assert prefs["lastBottle"]["bottleUnits"] == "ml"

    def test_log_bottle_feeding_breast_milk(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging breast milk bottle feeding."""
        # Log breast milk bottle
        api.log_bottle_feeding(child_uid, amount=90.0, bottle_type="Breast Milk", units="ml")
        time.sleep(2)

        # Check interval created with correct type
        intervals_ref = (
            api._get_firestore_client()
            .collection("feed")
            .document(child_uid)
            .collection("intervals")
        )

        recent_intervals = (
            intervals_ref
            .order_by("start", direction=firestore.Query.DESCENDING)
            .limit(1)
            .get()
        )

        intervals_list = list(recent_intervals)
        assert len(intervals_list) > 0

        interval_data = intervals_list[0].to_dict()
        assert interval_data is not None
        assert interval_data["mode"] == "bottle"
        assert interval_data["bottleType"] == "Breast Milk"
        assert interval_data["amount"] == 90.0
        assert interval_data["units"] == "ml"

    def test_log_bottle_feeding_ounces(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging bottle feeding with ounces."""
        # Log with oz units
        api.log_bottle_feeding(child_uid, amount=4.0, bottle_type="Formula", units="oz")
        time.sleep(2)

        # Check interval uses oz units
        intervals_ref = (
            api._get_firestore_client()
            .collection("feed")
            .document(child_uid)
            .collection("intervals")
        )

        recent_intervals = (
            intervals_ref
            .order_by("start", direction=firestore.Query.DESCENDING)
            .limit(1)
            .get()
        )

        intervals_list = list(recent_intervals)
        assert len(intervals_list) > 0

        interval_data = intervals_list[0].to_dict()
        assert interval_data is not None
        assert interval_data["units"] == "oz"
        assert interval_data["amount"] == 4.0

    def test_log_bottle_feeding_mixed(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging mixed bottle feeding."""
        # Log mixed bottle
        api.log_bottle_feeding(child_uid, amount=100.0, bottle_type="Mixed", units="ml")
        time.sleep(2)

        # Check interval created with Mixed type
        intervals_ref = (
            api._get_firestore_client()
            .collection("feed")
            .document(child_uid)
            .collection("intervals")
        )

        recent_intervals = (
            intervals_ref
            .order_by("start", direction=firestore.Query.DESCENDING)
            .limit(1)
            .get()
        )

        intervals_list = list(recent_intervals)
        assert len(intervals_list) > 0

        interval_data = intervals_list[0].to_dict()
        assert interval_data is not None
        assert interval_data["mode"] == "bottle"
        assert interval_data["bottleType"] == "Mixed"
        assert interval_data["amount"] == 100.0

    def test_log_bottle_feeding_default_params(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging bottle feeding with default parameters."""
        # Log with defaults (Formula, ml)
        api.log_bottle_feeding(child_uid, amount=150.0)
        time.sleep(2)

        # Check defaults applied
        intervals_ref = (
            api._get_firestore_client()
            .collection("feed")
            .document(child_uid)
            .collection("intervals")
        )

        recent_intervals = (
            intervals_ref
            .order_by("start", direction=firestore.Query.DESCENDING)
            .limit(1)
            .get()
        )

        intervals_list = list(recent_intervals)
        assert len(intervals_list) > 0

        interval_data = intervals_list[0].to_dict()
        assert interval_data is not None
        assert interval_data["mode"] == "bottle"
        assert interval_data["bottleType"] == "Formula"  # Default
        assert interval_data["units"] == "ml"  # Default
        assert interval_data["amount"] == 150.0

    def test_bottle_feeding_updates_prefs(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test that bottle feeding updates document-level preferences."""
        # Log bottle feeding
        api.log_bottle_feeding(child_uid, amount=110.0, bottle_type="Breast Milk", units="oz")
        time.sleep(2)

        # Check document-level prefs updated
        feed_doc = api._get_firestore_client().collection("feed").document(child_uid).get()
        data = feed_doc.to_dict()
        assert data is not None
        prefs = data.get("prefs", {})

        # Check document-level defaults
        assert prefs.get("bottleType") == "Breast Milk"
        assert prefs.get("bottleAmount") == 110.0
        assert prefs.get("bottleUnits") == "oz"

        # Check lastBottle
        assert "lastBottle" in prefs
        assert prefs["lastBottle"]["bottleType"] == "Breast Milk"
        assert prefs["lastBottle"]["bottleAmount"] == 110.0
        assert prefs["lastBottle"]["bottleUnits"] == "oz"
