"""Tests for the MCP server: unit tests with a recording fake API, plus live round trips."""

import os
from pathlib import Path
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from typing import cast
from typing import get_type_hints
from zoneinfo import ZoneInfo

import pytest
from fastmcp import Client
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth import AuthContext

from huckleberry_api import HuckleberryAPI
from huckleberry_api import mcp_server
from huckleberry_api.firebase_types import FirebaseChildDocument
from huckleberry_api.firebase_types import FirebaseMedicationTypeDocument
from huckleberry_api.firebase_types import FirebaseUserDocument
from huckleberry_api.models import HistoryEntry
from huckleberry_api.models import SolidsFoodReference

TZ = "Europe/Vilnius"


class FakeAPI:
    """Records every library call; returns canned data for reads."""

    def __init__(self, children: dict[str, str]) -> None:
        self.children = children
        self.running = True
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str):
        async def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record

    async def get_user(self) -> FirebaseUserDocument:
        return FirebaseUserDocument.model_validate({"childList": [{"cid": cid} for cid in self.children]})

    async def get_child(self, cid: str) -> FirebaseChildDocument:
        return FirebaseChildDocument(childsName=self.children[cid])

    async def list_medicine_types(self, cid: str) -> list[FirebaseMedicationTypeDocument]:
        return [
            FirebaseMedicationTypeDocument.model_validate(
                {"_id": "m1", "active": True, "mode": "medication", "name": "Vitamin D"}
            )
        ]

    async def get_tracker(self, cid: str, tracker: str) -> Any:
        if not self.running:
            return None
        active = SimpleNamespace(active=True)
        return SimpleNamespace(timer=SimpleNamespace(bath=active) if tracker == "activities" else active)

    async def list_entries(self, cid: str, tracker: str, start: datetime, end: datetime) -> list[HistoryEntry]:
        self.calls.append(("list_entries", (cid, tracker, start, end), {}))
        row = {"start": 1_800_000_000, "duration": 3600, "offset": -120}
        return [HistoryEntry.model_validate({"id": "abc", "tracker": "sleep", "data": row})]


def last_call(api: FakeAPI) -> tuple[str, tuple[Any, ...], dict[str, Any]]:
    return api.calls[-1]


def server(api: FakeAPI, allowed_emails: frozenset[str] = frozenset()) -> FastMCP:
    return mcp_server.create_server(cast(HuckleberryAPI, api), timezone=TZ, allowed_emails=allowed_emails)


async def call(api: FakeAPI, tool: str, **args: Any) -> Any:
    async with Client(server(api)) as client:
        return await client.call_tool(tool, args)


async def test_tool_list_and_annotations() -> None:
    async with Client(server(FakeAPI({"c1": "Ada"}))) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == {
        "list_children", "get_status", "sleep_timer", "log_sleep", "nursing_timer", "log_nursing", "log_bottle",
        "pump_timer", "log_pump", "activity_timer", "log_activity", "list_foods", "create_custom_food",
        "log_solids", "log_diaper", "log_potty", "log_growth", "log_temperature", "list_medicine_types",
        "create_medicine_type", "log_medicine", "list_entries", "update_entry", "delete_entry",
    }  # fmt: skip
    assert tools["list_entries"].annotations.read_only_hint is True
    assert tools["delete_entry"].annotations.destructive_hint is True
    bottle = tools["log_bottle"].input_schema["properties"]
    assert "Formula" in str(bottle["bottle_type"]) and "Goat Milk" in str(bottle["bottle_type"])


def test_inline_literals_match_library() -> None:
    assert get_type_hints(HuckleberryAPI.log_diaper)["pee_amount"] == mcp_server.DiaperAmount | None
    assert get_type_hints(HuckleberryAPI.log_potty)["poo_amount"] == mcp_server.DiaperAmount | None
    assert get_type_hints(HuckleberryAPI.log_growth)["units"] == mcp_server.GrowthUnits


async def test_naive_time_is_local_and_aware() -> None:
    api = FakeAPI({"c1": "Ada"})
    await call(api, "log_bottle", amount=120, start_time="2026-11-17T14:30")
    name, args, kwargs = last_call(api)
    assert (name, args) == ("log_bottle", ("c1",))
    assert kwargs["start_time"] == datetime(2026, 11, 17, 14, 30, tzinfo=ZoneInfo(TZ))
    assert kwargs | {"start_time": None} == {
        "start_time": None, "amount": 120, "bottle_type": "Formula", "units": "ml", "notes": None
    }  # fmt: skip


async def test_offset_time_kept_and_omitted_time_is_now() -> None:
    api = FakeAPI({"c1": "Ada"})
    await call(api, "log_diaper", mode="both", start_time="2026-11-17T12:00:00+00:00", poo_amount="big")
    assert last_call(api)[2]["start_time"].utcoffset().total_seconds() == 0
    await call(api, "log_temperature", amount=37.2, units="C")
    start = last_call(api)[2]["start_time"]
    assert start.tzinfo is not None and abs(start.timestamp() - time.time()) < 5


async def test_timer_actions_map_and_reject_unused_params() -> None:
    api = FakeAPI({"c1": "Ada"})
    await call(api, "sleep_timer", action="start")
    assert last_call(api) == ("start_sleep", ("c1",), {"start_time": None})
    await call(api, "nursing_timer", action="switch")
    assert last_call(api)[0] == "switch_nursing_side"
    await call(api, "activity_timer", action="complete", mode="bath", duration=600, notes="fun")
    assert last_call(api) == (
        "complete_activity", ("c1", "bath"), {"start_time": None, "end_time": None, "duration": 600, "notes": "fun"}
    )  # fmt: skip
    with pytest.raises(ToolError, match="end_time"):
        await call(api, "pump_timer", action="pause", end_time="2026-11-17T10:00")


async def test_timer_actions_fail_when_nothing_is_running() -> None:
    api = FakeAPI({"c1": "Ada"})
    api.running = False
    for tool, args in (
        ("sleep_timer", {"action": "complete"}),
        ("nursing_timer", {"action": "pause"}),
        ("pump_timer", {"action": "cancel"}),
        ("activity_timer", {"action": "switch", "mode": "bath"}),
    ):
        with pytest.raises(ToolError, match="timer is running"):
            await call(api, tool, **args)
    assert not [c for c in api.calls if c[0] != "list_entries"]
    await call(api, "pump_timer", action="start")
    assert last_call(api)[0] == "start_pump"


async def test_bad_enum_and_bad_time_rejected(caplog: pytest.LogCaptureFixture) -> None:
    api = FakeAPI({"c1": "Ada"})
    with pytest.raises(ToolError):
        await call(api, "log_bottle", amount=1, bottle_type="Juice")
    with pytest.raises(ToolError, match="ISO 8601"):
        await call(api, "log_bottle", amount=1, start_time="yesterday")
    assert api.calls == []
    assert "ISO 8601" in caplog.text  # failure reasons reach the server log


async def test_child_resolution() -> None:
    api = FakeAPI({"c1": "Ada", "c2": "Bo"})
    with pytest.raises(ToolError, match="Ada, Bo"):
        await call(api, "log_diaper", mode="pee")
    await call(api, "log_diaper", mode="pee", child="bo")
    assert last_call(api)[1] == ("c2",)
    await call(api, "log_diaper", mode="pee", child="c1")
    assert last_call(api)[1] == ("c1",)
    with pytest.raises(ToolError, match="No single child"):
        await call(api, "log_diaper", mode="pee", child="Cy")


async def test_log_medicine_by_name_and_solids() -> None:
    api = FakeAPI({"c1": "Ada"})
    await call(api, "log_medicine", name="vitamin d", amount=1, units="drops")
    assert last_call(api)[2]["medicine_type"].id_ == "m1"
    with pytest.raises(ToolError, match="Vitamin D"):
        await call(api, "log_medicine", name="Tylenol")
    await call(api, "log_solids", foods=[{"id": "f1", "source": "curated", "name": "Avocado", "amount": "2 spoons"}])
    assert last_call(api)[2]["foods"] == [SolidsFoodReference(id="f1", source="curated", name="Avocado",
                                                              amount="2 spoons")]  # fmt: skip


async def test_list_entries_range_and_local_times() -> None:
    api = FakeAPI({"c1": "Ada"})
    result = await call(api, "list_entries", tracker="sleep", end="2026-11-17T08:00")
    _, (cid, tracker, start, end), _ = last_call(api)
    assert (cid, tracker) == ("c1", "sleep")
    assert end == datetime(2026, 11, 17, 8, tzinfo=ZoneInfo(TZ)) and (end - start).total_seconds() == 86400
    entry = result.structured_content["result"][0]
    assert entry["id"] == "abc" and entry["data"]["start"] == 1_800_000_000
    assert entry["start_local"] == datetime.fromtimestamp(1_800_000_000, ZoneInfo(TZ)).isoformat()
    assert entry["end_local"] == datetime.fromtimestamp(1_800_003_600, ZoneInfo(TZ)).isoformat()


async def test_library_error_surfaces_as_tool_error() -> None:
    api = FakeAPI({"c1": "Ada"})

    async def fail(*args: Any, **kwargs: Any) -> None:
        raise ValueError("end_time must be after start_time")

    api.log_sleep = fail  # type: ignore[method-assign]
    with pytest.raises(ToolError, match="end_time must be after start_time"):
        await call(api, "log_sleep", start_time="2026-11-17T10:00", end_time="2026-11-17T09:00")


def _ctx(claims: dict[str, Any] | None) -> AuthContext:
    token = None if claims is None else AccessToken(token="t", client_id="c", scopes=[], claims=claims)
    return AuthContext(token=token, component=None)  # type: ignore[arg-type]


def test_email_allowlist_fails_closed() -> None:
    check = mcp_server.email_allowlist_check(frozenset({"mom@example.com"}))
    assert check(_ctx({"email": "Mom@Example.com"}))
    assert not check(_ctx({"email": "stranger@example.com"}))
    assert not check(_ctx({"email": "mom@example.com", "email_verified": False}))
    assert not check(_ctx({}))
    assert not check(_ctx(None))


async def test_allowlist_middleware_hides_tools_without_token() -> None:
    async with Client(server(FakeAPI({"c1": "Ada"}), frozenset({"mom@example.com"}))) as client:
        assert await client.list_tools() == []


def test_load_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".config").mkdir()
    (tmp_path / ".config" / ".env").write_text(
        "# comment\n=orphan\nHB_TEST_Q=secret\"\nHB_TEST_A=from-file\nexport HB_TEST_B='quoted value'\nHB_TEST_EMPTY=\nHB_TEST_SET=from-file\n"
    )
    monkeypatch.chdir(tmp_path)
    for key in ("HB_TEST_A", "HB_TEST_B", "HB_TEST_EMPTY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HB_TEST_SET", "from-env")
    mcp_server.load_env_file()
    try:
        assert os.environ["HB_TEST_A"] == "from-file"
        assert os.environ.pop("HB_TEST_Q") == 'secret"'
        assert os.environ["HB_TEST_B"] == "quoted value"
        assert "HB_TEST_EMPTY" not in os.environ
        assert os.environ["HB_TEST_SET"] == "from-env"
    finally:
        os.environ.pop("HB_TEST_A", None)
        os.environ.pop("HB_TEST_B", None)


def test_auth_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CLERK_DOMAIN",
        "CLERK_OAUTH_CLIENT_ID",
        "CLERK_OAUTH_CLIENT_SECRET",
        "HUCKLEBERRY_MCP_NO_AUTH",
        "HUCKLEBERRY_MCP_ALLOWED_EMAILS",
        "HUCKLEBERRY_MCP_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit, match="Refusing to start"):
        mcp_server._auth_from_env()
    monkeypatch.setenv("CLERK_DOMAIN", "x.clerk.accounts.dev")
    monkeypatch.setenv("CLERK_OAUTH_CLIENT_ID", "id")
    monkeypatch.setenv("CLERK_OAUTH_CLIENT_SECRET", "secret")
    with pytest.raises(SystemExit):  # base URL still missing
        mcp_server._auth_from_env()
    monkeypatch.setenv("HUCKLEBERRY_MCP_BASE_URL", "https://hb.tail1234.ts.net/mcp/")
    with pytest.raises(SystemExit, match="ALLOWED_EMAILS"):  # Clerk without an allowlist
        mcp_server._auth_from_env()
    monkeypatch.setenv("HUCKLEBERRY_MCP_ALLOWED_EMAILS", " Mom@Example.com, dad@example.com ")
    auth, allowed = mcp_server._auth_from_env()
    assert auth is not None and allowed == {"mom@example.com", "dad@example.com"}
    for key in ("CLERK_DOMAIN", "CLERK_OAUTH_CLIENT_ID", "CLERK_OAUTH_CLIENT_SECRET"):
        monkeypatch.delenv(key)
    monkeypatch.setenv("HUCKLEBERRY_MCP_NO_AUTH", "1")
    assert mcp_server._auth_from_env()[0] is None


@pytest.mark.parametrize(
    ("tool", "args", "tracker"),
    [
        ("log_bottle", {"amount": 11, "notes": "mcp live test"}, "feed"),
        ("log_diaper", {"mode": "pee", "notes": "mcp live test"}, "diaper"),
        ("log_temperature", {"amount": 36.6, "units": "C", "notes": "mcp live test"}, "health"),
    ],
)
async def test_live_log_list_delete(api: HuckleberryAPI, child_uid: str, tool: str, args: dict, tracker: str) -> None:
    timezone = os.environ["HUCKLEBERRY_TIMEZONE"]
    start = datetime.now(ZoneInfo(timezone)).replace(microsecond=0).isoformat()
    async with Client(mcp_server.create_server(api, timezone=timezone)) as client:
        await client.call_tool(tool, {**args, "start_time": start, "child": child_uid})
        entry_id = None
        try:
            listed = await client.call_tool("list_entries", {"tracker": tracker, "child": child_uid})
            ours = [e for e in listed.structured_content["result"] if e["start_local"] == start]
            assert len(ours) == 1
            entry_id = ours[0]["id"]
        finally:
            if entry_id is not None:
                await client.call_tool("delete_entry", {"tracker": tracker, "entry_id": entry_id, "child": child_uid})
