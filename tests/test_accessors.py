"""Tests for the child-scoped accessor facade.

These assert delegation only: the handles are stateless, so a recording fake API
is enough and no Firestore or credentials are involved.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast

from huckleberry_api import HuckleberryAPI
from huckleberry_api.accessors import ChildHandle

START = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
END = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)


class FakeAPI:
    """Records calls; watch_* is sync, everything else is async."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.result = object()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("watch_"):

            def watch(child_uid: str) -> object:
                self.calls.append((name, (child_uid,), {}))
                return self.result

            return watch

        async def call(*args: Any, **kwargs: Any) -> object:
            self.calls.append((name, args, kwargs))
            return self.result

        return call


def make_child() -> tuple[FakeAPI, ChildHandle]:
    fake = FakeAPI()
    return fake, ChildHandle(cast(Any, fake), "kid")


def test_child_handle_binds_uid_without_io() -> None:
    fake, child = make_child()

    assert child.child_uid == "kid"
    assert child.sleep.child_uid == "kid"
    assert child.feeding.nursing.child_uid == "kid"
    assert fake.calls == []


def test_sleep_handle_delegates() -> None:
    fake, child = make_child()

    assert child.sleep.watch() is fake.result
    assert fake.calls[-1] == ("watch_sleep", ("kid",), {})

    asyncio.run(child.sleep.state())
    assert fake.calls[-1] == ("get_sleep", ("kid",), {})

    asyncio.run(child.sleep.start(start_time=START))
    assert fake.calls[-1] == ("start_sleep", ("kid",), {"start_time": START})

    asyncio.run(child.sleep.complete(end_time=END))
    assert fake.calls[-1] == ("complete_sleep", ("kid",), {"start_time": None, "end_time": END, "details": None})

    asyncio.run(child.sleep.log(start_time=START, end_time=END))
    assert fake.calls[-1] == ("log_sleep", ("kid",), {"start_time": START, "end_time": END, "details": None})

    asyncio.run(child.sleep.history(START, END))
    assert fake.calls[-1] == ("list_sleep_intervals", ("kid", START, END), {})


def test_feeding_handles_delegate() -> None:
    fake, child = make_child()

    assert child.feeding.watch() is fake.result
    asyncio.run(child.feeding.state())
    asyncio.run(child.feeding.history(START, END))
    asyncio.run(child.feeding.nursing.start("right", start_time=START))
    asyncio.run(child.feeding.nursing.resume("left"))
    asyncio.run(child.feeding.nursing.switch_side())
    asyncio.run(child.feeding.nursing.log(start_time=START, end_time=END, left_duration=600))
    asyncio.run(child.feeding.bottle.log(start_time=START, amount=120, units="ml"))
    asyncio.run(child.feeding.solids.curated_foods())
    asyncio.run(child.feeding.solids.custom_foods(include_archived=True))
    asyncio.run(child.feeding.solids.create_custom_food("pear", image="x"))
    asyncio.run(child.feeding.solids.log(start_time=START, foods=[]))

    names = [name for name, _, _ in fake.calls]
    assert names == [
        "watch_feed",
        "get_nursing",
        "list_feed_intervals",
        "start_nursing",
        "resume_nursing",
        "switch_nursing_side",
        "log_nursing",
        "log_bottle",
        "list_solids_curated_foods",
        "list_solids_custom_foods",
        "create_solids_custom_food",
        "log_solids",
    ]
    assert fake.calls[3][1] == ("kid", "right")
    assert fake.calls[9][1] == ("kid", True)


def test_pump_activity_diaper_health_delegate() -> None:
    fake, child = make_child()

    assert child.pump.watch() is fake.result
    asyncio.run(child.pump.complete(total_amount=90))
    asyncio.run(child.pump.log(start_time=START, duration=600))
    asyncio.run(child.pump.history(START, END))

    assert child.activity.watch() is fake.result
    asyncio.run(child.activity.start("bath", start_time=START))
    asyncio.run(child.activity.switch("bath"))
    asyncio.run(child.activity.history(START, END))

    assert child.diaper.watch() is fake.result
    asyncio.run(child.diaper.log(start_time=START, mode="pee"))
    asyncio.run(child.diaper.log_potty(start_time=START, mode="pee", how_it_happened="accident"))
    asyncio.run(child.diaper.history(START, END))

    assert child.health.watch() is fake.result
    asyncio.run(child.health.log_growth(start_time=START, weight=5.5))
    asyncio.run(child.health.latest_growth())
    asyncio.run(child.health.create_medicine_type("vitamin"))
    asyncio.run(child.health.history(START, END))

    names = [name for name, _, _ in fake.calls]
    assert names == [
        "watch_pump",
        "complete_pump",
        "log_pump",
        "list_pump_intervals",
        "watch_activity",
        "start_activity",
        "switch_activity",
        "list_activity_intervals",
        "watch_diaper",
        "log_diaper",
        "log_potty",
        "list_diaper_intervals",
        "watch_health",
        "log_growth",
        "get_latest_growth",
        "create_medicine_type",
        "list_health_entries",
    ]

    # Payloads matter as much as the method name: a dropped or renamed
    # argument would otherwise pass unnoticed.
    assert fake.calls[1] == (
        "complete_pump",
        ("kid",),
        {
            "start_time": None,
            "end_time": None,
            "left_amount": None,
            "right_amount": None,
            "total_amount": 90,
            "units": None,
            "notes": None,
        },
    )
    assert fake.calls[2] == (
        "log_pump",
        ("kid",),
        {
            "start_time": START,
            "duration": 600,
            "left_amount": None,
            "right_amount": None,
            "total_amount": None,
            "units": "ml",
            "notes": None,
        },
    )
    assert fake.calls[5] == ("start_activity", ("kid", "bath"), {"start_time": START})
    assert fake.calls[9] == (
        "log_diaper",
        ("kid",),
        {
            "start_time": START,
            "mode": "pee",
            "pee_amount": None,
            "poo_amount": None,
            "color": None,
            "consistency": None,
            "diaper_rash": False,
            "notes": None,
        },
    )
    assert fake.calls[13] == (
        "log_growth",
        ("kid",),
        {"start_time": START, "weight": 5.5, "height": None, "head": None, "units": "metric"},
    )


def test_child_profile_delegates() -> None:
    fake, child = make_child()

    assert child.watch() is fake.result
    asyncio.run(child.profile())

    assert fake.calls == [("watch_child", ("kid",), {}), ("get_child", ("kid",), {})]


def test_api_child_returns_a_bound_handle() -> None:
    api = HuckleberryAPI(
        email="test@example.com",
        password="password",
        timezone="UTC",
        websession=SimpleNamespace(),  # type: ignore[arg-type]
    )

    child = api.child("kid")

    assert isinstance(child, ChildHandle)
    assert child.child_uid == "kid"
    assert child.sleep.child_uid == "kid"
