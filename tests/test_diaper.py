"""Diaper tracking tests for Huckleberry API."""
import time

from huckleberry_api import HuckleberryAPI


class TestDiaperTracking:
    """Test diaper tracking functionality."""

    def test_log_diaper_pee(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging pee-only diaper change."""
        api.log_diaper(child_uid, mode="pee", pee_amount="medium")
        time.sleep(1)

        # Verify it was logged
        diaper_doc = api._get_firestore_client().collection("diaper").document(child_uid).get()
        data = diaper_doc.to_dict()
        assert data is not None
        assert "lastDiaper" in data.get("prefs", {})
        assert data["prefs"]["lastDiaper"]["mode"] == "pee"

    def test_log_diaper_poo(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging poo-only diaper change."""
        api.log_diaper(
            child_uid,
            mode="poo",
            poo_amount="big",
            color="yellow",
            consistency="solid"
        )
        time.sleep(1)

        diaper_doc = api._get_firestore_client().collection("diaper").document(child_uid).get()
        data = diaper_doc.to_dict()
        assert data is not None
        assert data["prefs"]["lastDiaper"]["mode"] == "poo"

    def test_log_diaper_both(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging both pee and poo."""
        api.log_diaper(
            child_uid,
            mode="both",
            pee_amount="medium",
            poo_amount="medium",
            color="green",
            consistency="runny"
        )
        time.sleep(1)

        diaper_doc = api._get_firestore_client().collection("diaper").document(child_uid).get()
        data = diaper_doc.to_dict()
        assert data is not None
        assert data["prefs"]["lastDiaper"]["mode"] == "both"

    def test_log_diaper_dry(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging dry diaper check."""
        api.log_diaper(child_uid, mode="dry")
        time.sleep(1)

        diaper_doc = api._get_firestore_client().collection("diaper").document(child_uid).get()
        data = diaper_doc.to_dict()
        assert data is not None
        assert data["prefs"]["lastDiaper"]["mode"] == "dry"

    def test_log_potty_pee(self, api: HuckleberryAPI, child_uid: str) -> None:
        """Test logging potty event with default how_it_happened."""
        api.log_diaper(child_uid, mode="pee", pee_amount="little", is_potty=True)
        time.sleep(1)

        diaper_doc = api._get_firestore_client().collection("diaper").document(child_uid).get()
        data = diaper_doc.to_dict()

        assert data is not None
        assert data["prefs"]["lastPotty"]["mode"] == "pee"