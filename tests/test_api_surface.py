"""Static guards for the mixin-composed public API."""

from __future__ import annotations

from huckleberry_api import HuckleberryAPI
from huckleberry_api.client import HuckleberryBase
from huckleberry_api.listeners import ListenerMixin

# Derived from the class itself so a newly added mixin cannot escape the guard.
MIXINS = HuckleberryAPI.__bases__

EXPECTED_PUBLIC_METHODS = {
    "authenticate",
    "cancel_activity",
    "cancel_nursing",
    "cancel_pump",
    "cancel_sleep",
    "complete_activity",
    "complete_nursing",
    "complete_pump",
    "complete_sleep",
    "create_medicine_type",
    "create_solids_custom_food",
    "ensure_session",
    "get_child",
    "get_latest_growth",
    "get_nursing",
    "get_sleep",
    "get_user",
    "list_activity_intervals",
    "list_diaper_intervals",
    "list_feed_intervals",
    "list_health_entries",
    "list_medicine_types",
    "list_pump_intervals",
    "list_sleep_intervals",
    "list_solids_curated_foods",
    "list_solids_custom_foods",
    "log_activity",
    "log_bottle",
    "log_diaper",
    "log_growth",
    "log_medicine",
    "log_nursing",
    "log_potty",
    "log_pump",
    "log_sleep",
    "log_solids",
    "log_temperature",
    "pause_activity",
    "pause_nursing",
    "pause_pump",
    "pause_sleep",
    "refresh_session_token",
    "resume_activity",
    "resume_nursing",
    "resume_pump",
    "resume_sleep",
    "setup_activity_listener",
    "setup_child_listener",
    "setup_diaper_listener",
    "setup_feed_listener",
    "setup_health_listener",
    "setup_pump_listener",
    "setup_sleep_listener",
    "start_activity",
    "start_nursing",
    "start_pump",
    "start_sleep",
    "stop_all_listeners",
    "switch_activity",
    "switch_nursing_side",
}


def test_public_api_surface_is_complete() -> None:
    """Every public method of the original class is still exposed."""
    actual = {name for name in dir(HuckleberryAPI) if not name.startswith("_")}
    assert actual == EXPECTED_PUBLIC_METHODS


def test_listener_recreation_hook_comes_from_listener_mixin() -> None:
    """Base-class ordering must not let the no-op hook win after a token refresh."""
    assert "_recreate_listeners" in ListenerMixin.__dict__, (
        "ListenerMixin must implement the hook, not inherit the no-op"
    )
    assert HuckleberryAPI._recreate_listeners is ListenerMixin.__dict__["_recreate_listeners"]


def test_no_mixin_shadows_a_base_or_another_mixin_method() -> None:
    """A duplicate definition would silently shadow another feature's method."""
    owners: dict[str, type] = {name: HuckleberryBase for name in HuckleberryBase.__dict__ if not name.startswith("__")}
    for mixin in MIXINS:
        for name in mixin.__dict__:
            if name.startswith("__") or name == "_recreate_listeners":
                continue
            assert name not in owners, f"{name} defined by {owners[name].__name__} and {mixin.__name__}"
            owners[name] = mixin
