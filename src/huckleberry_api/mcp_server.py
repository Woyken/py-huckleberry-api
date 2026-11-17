"""Remote MCP server exposing :class:`HuckleberryAPI` to Claude as a custom connector.

Run ``huckleberry-mcp`` for Streamable HTTP at ``/mcp`` (behind Clerk OAuth) or
``huckleberry-mcp --stdio`` for local clients. Configuration is read from the environment, then from
``.config/.env`` in the working directory for anything not already set:

    HUCKLEBERRY_EMAIL, HUCKLEBERRY_PASSWORD, HUCKLEBERRY_TIMEZONE   required
    HUCKLEBERRY_MCP_HOST (127.0.0.1), HUCKLEBERRY_MCP_PORT (8765)   listen address
    HUCKLEBERRY_MCP_BASE_URL                                        public https origin
    CLERK_DOMAIN, CLERK_OAUTH_CLIENT_ID, CLERK_OAUTH_CLIENT_SECRET  OAuth
    HUCKLEBERRY_MCP_ALLOWED_EMAILS                                  comma-separated emails allowed in
    HUCKLEBERRY_MCP_NO_AUTH=1                                       explicitly serve HTTP without auth
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import AsyncIterator
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Annotated
from typing import Any
from typing import Literal
from typing import get_args
from zoneinfo import ZoneInfo

import aiohttp
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthContext
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.providers.clerk import ClerkProvider
from fastmcp.server.middleware import AuthMiddleware
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from pydantic import Field
from pydantic import JsonValue

from .api import HuckleberryAPI
from .firebase_types import ActivityMode
from .firebase_types import BottleType
from .firebase_types import DiaperMode
from .firebase_types import FeedSide
from .firebase_types import FirebaseSleepDetails
from .firebase_types import MedicationUnits
from .firebase_types import PooColor
from .firebase_types import PooConsistency
from .firebase_types import PottyResult
from .firebase_types import SolidsReaction
from .firebase_types import TemperatureUnits
from .firebase_types import VolumeUnits
from .models import HistoryEntry
from .models import SolidsFoodReference
from .models import Tracker

_LOGGER = logging.getLogger(__name__)

# Mirrors the inline literals in HuckleberryAPI.log_diaper/log_potty and log_growth signatures
# (tests assert they stay equal); firebase_types has no named alias for them.
DiaperAmount = Literal["little", "medium", "big"]
GrowthUnits = Literal["metric", "imperial"]

Child = Annotated[str | None, Field(description="Child's name or id. Omit when the family has only one child.")]
StartTime = Annotated[
    str | None,
    Field(description="When it started, ISO 8601 (e.g. 2026-11-17T14:30). No offset = server timezone. Omit = now."),
]
EndTime = Annotated[
    str | None,
    Field(description="When it ended, ISO 8601. No offset = server timezone. Omit = now."),
]
Notes = Annotated[str | None, Field(description="Optional free-text note.")]
Seconds = Annotated[float | None, Field(description="Duration in seconds.", ge=0)]

TrackerArg = Annotated[
    Tracker,
    Field(
        description="Which tracker: sleep, feed (nursing, bottle and "
        "solids), diaper (diapers and potty), health (growth, temperature, "
        "medicine), pump, or activities."
    ),
]

_READ = {"readOnlyHint": True}
_DESTRUCTIVE = {"destructiveHint": True}


class _Clock:
    """Converts between the user's ISO strings and timezone-aware datetimes."""

    def __init__(self, timezone: str) -> None:
        self.tz = ZoneInfo(timezone)

    def parse(self, value: str | None) -> datetime:
        """Parse an ISO 8601 string; naive values are local, ``None`` is now."""
        if value is None:
            return datetime.now(self.tz)
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as err:
            raise ToolError(f"Invalid time {value!r}: use ISO 8601, e.g. 2026-11-17T14:30") from err
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=self.tz)

    def maybe(self, value: str | None) -> datetime | None:
        """Parse an optional time, keeping ``None`` so the library applies its own default."""
        return None if value is None else self.parse(value)

    def iso(self, seconds: float) -> str:
        """Format epoch seconds as a local ISO string."""
        return datetime.fromtimestamp(seconds, self.tz).isoformat(timespec="seconds")


def _only(action: str, allowed: set[str], **given: object) -> None:
    """Reject parameters that the chosen timer action does not use."""
    extra = sorted(name for name, value in given.items() if value is not None and name not in allowed)
    if extra:
        raise ToolError(f"{', '.join(extra)} cannot be used with action {action!r}")


def _dump(model: Any) -> Any:
    """Serialize a Firebase model with its raw field names."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True) if model is not None else None


def email_allowlist_check(allowed: frozenset[str]) -> Callable[[AuthContext], bool]:
    """Build an auth check allowing only tokens whose verified email is in ``allowed`` (lowercase)."""

    def check(ctx: AuthContext) -> bool:
        claims = ctx.token.claims if ctx.token is not None else {}
        email = claims.get("email")
        verified = claims.get("email_verified") is not False
        return verified and isinstance(email, str) and email.strip().lower() in allowed

    return check


def create_server(
    api: HuckleberryAPI | None = None,
    *,
    timezone: str | None = None,
    auth: AuthProvider | None = None,
    allowed_emails: frozenset[str] = frozenset(),
) -> FastMCP:
    """Create the MCP server.

    Args:
        api: Authenticated client. When omitted, one is built from the environment at startup.
        timezone: IANA timezone for naive input times and output. Defaults to ``HUCKLEBERRY_TIMEZONE``.
        auth: FastMCP auth provider for HTTP transport.
        allowed_emails: Lowercase emails allowed to use the tools; empty disables the check (no-auth, stdio).
    """
    tz_name = timezone or os.environ["HUCKLEBERRY_TIMEZONE"]
    clock = _Clock(tz_name)
    client = api

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        nonlocal client
        if client is not None:
            yield
            return
        async with aiohttp.ClientSession() as session:
            client = HuckleberryAPI(
                email=os.environ["HUCKLEBERRY_EMAIL"],
                password=os.environ["HUCKLEBERRY_PASSWORD"],
                timezone=tz_name,
                websession=session,
            )
            await client.authenticate()
            _LOGGER.info("Authenticated with Huckleberry")
            try:
                yield
            finally:
                await client.stop_all_listeners()
                client = None

    def hb() -> HuckleberryAPI:
        if client is None:
            raise ToolError("Huckleberry client is not ready")
        return client

    async def children() -> list[tuple[str, str]]:
        user = await hb().get_user()
        if user is None or not user.childList:
            raise ToolError("No children found in this Huckleberry account")
        result = []
        for ref in user.childList:
            doc = await hb().get_child(ref.cid)
            result.append((ref.cid, (doc.childsName if doc else None) or ref.nickname or ref.cid))
        return result

    async def resolve(child: str | None) -> str:
        found = await children()
        if child is None and len(found) == 1:
            return found[0][0]
        wanted = (child or "").strip().lower()
        matches = [cid for cid, name in found if wanted in (cid.lower(), name.lower())]
        if child is not None and len(matches) == 1:
            return matches[0]
        names = ", ".join(name for _, name in found)
        if child is None:
            raise ToolError(f"Several children: say which one ({names})")
        raise ToolError(f"No single child matches {child!r}; children are: {names}")

    async def require_running(cid: str, tracker: Tracker, action: str, mode: str | None = None) -> None:
        """Refuse timer actions the library silently ignores because no timer is running."""
        if action == "start":
            return
        timers = getattr(await hb().get_tracker(cid, tracker), "timer", None)
        if tracker == "activities":
            modes: tuple[str, ...] = get_args(ActivityMode) if action == "switch" else (mode or "",)
            running = any(getattr(getattr(timers, m, None), "active", False) for m in modes)
        else:
            running = getattr(timers, "active", False)
        if not running:
            raise ToolError(f"No {mode or tracker} timer is running, so there is nothing to {action}")

    def entry_out(entry: HistoryEntry) -> dict[str, Any]:
        data = entry.data
        out: dict[str, Any] = {"id": entry.id, "tracker": entry.tracker, "start_local": clock.iso(data.start)}
        seconds = getattr(data, "duration", None) or (
            (getattr(data, "leftDuration", None) or 0) + (getattr(data, "rightDuration", None) or 0)
        )
        if seconds:
            out["end_local"] = clock.iso(data.start + seconds)
        out["data"] = _dump(data)
        return out

    mcp = FastMCP(
        "Huckleberry",
        instructions=(
            f"Logs and edits baby-care data in the family's Huckleberry app. All times are in {tz_name}: "
            "send ISO 8601 times without an offset to mean local time, or omit a time to mean now. "
            "Timers (sleep, nursing, pump, activity) mirror the app's live timers; log_* tools save finished "
            "events directly. To fix or remove something: call list_entries for the tracker and time range, "
            "confirm the right entry with the user, then call update_entry or delete_entry with its id. "
            "Use get_status to see running timers and the latest entry of each kind."
        ),
        lifespan=lifespan,
        auth=auth,
        middleware=[
            # Log why a tool call failed; FastMCP itself logs only the tool name.
            ErrorHandlingMiddleware(logger=_LOGGER, transform_errors=False),
            *([AuthMiddleware(auth=email_allowlist_check(allowed_emails))] if allowed_emails else []),
        ],
    )

    # --- account / status -------------------------------------------------------------------------

    @mcp.tool(annotations=_READ)
    async def list_children() -> list[dict[str, Any]]:
        """List the children in the Huckleberry account with their ids, names, birthdates and gender."""
        result = []
        for cid, name in await children():
            doc = await hb().get_child(cid)
            result.append(
                {
                    "id": cid,
                    "name": name,
                    "birthdate": doc.birthdate if doc else None,
                    "gender": doc.gender if doc else None,
                }
            )
        return result

    @mcp.tool(annotations=_READ)
    async def get_status(child: Child = None) -> dict[str, Any]:
        """Show what is happening now: running timers and the latest entry of each kind (last sleep, feed,
        bottle, solids, diaper, potty, pump, activity, growth, temperature, medicine), plus the current local
        time. Raw Firebase fields; timestamps in them are epoch seconds or milliseconds."""
        cid = await resolve(child)
        trackers = get_args(Tracker)
        docs = await asyncio.gather(*(hb().get_tracker(cid, tracker) for tracker in trackers))
        return {
            "now_local": datetime.now(clock.tz).isoformat(timespec="seconds"),
            "timezone": tz_name,
            **{tracker: _dump(doc) for tracker, doc in zip(trackers, docs, strict=True)},
        }

    # --- sleep ------------------------------------------------------------------------------------

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def sleep_timer(
        action: Annotated[
            Literal["start", "pause", "resume", "cancel", "complete"],
            Field(
                description="start (or move the start of a running timer), pause, resume, "
                "cancel (discard without saving), complete (stop and save)."
            ),
        ],
        start_time: StartTime = None,
        end_time: Annotated[
            str | None,
            Field(description="complete only: when sleep ended, ISO 8601. Omit = now (or the pause time if paused)."),
        ] = None,
        details: Annotated[
            FirebaseSleepDetails | None,
            Field(description="complete only: how the child fell asleep / woke, where, and notes."),
        ] = None,
        child: Child = None,
    ) -> str:
        """Control the live sleep timer. Use for "baby fell asleep" / "baby woke up" in real time;
        use log_sleep for a sleep that is already over. cancel discards the running sleep."""
        cid = await resolve(child)
        api_ = hb()
        _only(action, {"start_time"} if action in ("start", "complete") else set(), start_time=start_time)
        _only(action, {"end_time", "details"} if action == "complete" else set(), end_time=end_time, details=details)
        await require_running(cid, "sleep", action)
        if action == "start":
            await api_.start_sleep(cid, start_time=clock.maybe(start_time))
        elif action == "pause":
            await api_.pause_sleep(cid)
        elif action == "resume":
            await api_.resume_sleep(cid)
        elif action == "cancel":
            await api_.cancel_sleep(cid)
        else:
            await api_.complete_sleep(
                cid, start_time=clock.maybe(start_time), end_time=clock.maybe(end_time), details=details
            )
        return f"Sleep timer: {action} done"

    @mcp.tool
    async def log_sleep(
        start_time: Annotated[str, Field(description="When sleep started, ISO 8601. No offset = server timezone.")],
        end_time: EndTime = None,
        details: Annotated[
            FirebaseSleepDetails | None, Field(description="How the child fell asleep / woke, where, and notes.")
        ] = None,
        child: Child = None,
    ) -> str:
        """Save a sleep that already happened, without touching the live timer."""
        cid = await resolve(child)
        start, end = clock.parse(start_time), clock.parse(end_time)
        await hb().log_sleep(cid, start_time=start, end_time=end, details=details)
        return f"Logged sleep {start.isoformat(timespec='minutes')} to {end.isoformat(timespec='minutes')}"

    # --- feeding ----------------------------------------------------------------------------------

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def nursing_timer(
        action: Annotated[
            Literal["start", "pause", "resume", "switch", "cancel", "complete"],
            Field(
                description="start (or move the start of a running session), pause, resume, switch sides, "
                "cancel (discard without saving), complete (stop and save)."
            ),
        ],
        side: Annotated[
            FeedSide | None,
            Field(
                description="start/resume only: breast side. start defaults to "
                "left; resume defaults to the side last used."
            ),
        ] = None,
        start_time: StartTime = None,
        end_time: Annotated[
            str | None, Field(description="complete only: when nursing ended, ISO 8601. Omit = now.")
        ] = None,
        child: Child = None,
    ) -> str:
        """Control the live breastfeeding (nursing) timer. Use log_nursing for a feed that is already over."""
        cid = await resolve(child)
        api_ = hb()
        _only(action, {"side"} if action in ("start", "resume") else set(), side=side)
        _only(action, {"start_time"} if action in ("start", "complete") else set(), start_time=start_time)
        _only(action, {"end_time"} if action == "complete" else set(), end_time=end_time)
        await require_running(cid, "feed", action)
        if action == "start":
            await api_.start_nursing(cid, side or "left", start_time=clock.maybe(start_time))
        elif action == "pause":
            await api_.pause_nursing(cid)
        elif action == "resume":
            await api_.resume_nursing(cid, side)
        elif action == "switch":
            await api_.switch_nursing_side(cid)
        elif action == "cancel":
            await api_.cancel_nursing(cid)
        else:
            await api_.complete_nursing(cid, start_time=clock.maybe(start_time), end_time=clock.maybe(end_time))
        return f"Nursing timer: {action} done"

    @mcp.tool
    async def log_nursing(
        start_time: Annotated[str, Field(description="When nursing started, ISO 8601. No offset = server timezone.")],
        end_time: EndTime = None,
        side: Annotated[FeedSide, Field(description="Side nursed last.")] = "left",
        left_duration: Annotated[
            float | None, Field(description="Seconds on the left breast. Give together with right_duration.", ge=0)
        ] = None,
        right_duration: Annotated[
            float | None, Field(description="Seconds on the right breast. Give together with left_duration.", ge=0)
        ] = None,
        notes: Notes = None,
        child: Child = None,
    ) -> str:
        """Save a breastfeed that already happened, without the live timer."""
        cid = await resolve(child)
        start, end = clock.parse(start_time), clock.parse(end_time)
        await hb().log_nursing(
            cid,
            start_time=start,
            end_time=end,
            side=side,
            left_duration=left_duration,
            right_duration=right_duration,
            notes=notes,
        )
        return f"Logged nursing {start.isoformat(timespec='minutes')} to {end.isoformat(timespec='minutes')}"

    @mcp.tool
    async def log_bottle(
        amount: Annotated[float, Field(description="Amount fed, in `units`.", ge=0)],
        units: Annotated[VolumeUnits, Field(description="Volume units.")] = "ml",
        bottle_type: Annotated[BottleType, Field(description="What was in the bottle.")] = "Formula",
        start_time: StartTime = None,
        notes: Notes = None,
        child: Child = None,
    ) -> str:
        """Log a bottle feed (formula, pumped breast milk, etc.)."""
        cid = await resolve(child)
        start = clock.parse(start_time)
        await hb().log_bottle(cid, start_time=start, amount=amount, bottle_type=bottle_type, units=units, notes=notes)
        return f"Logged {amount:g} {units} {bottle_type} bottle at {start.isoformat(timespec='minutes')}"

    # --- pump -------------------------------------------------------------------------------------

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def pump_timer(
        action: Annotated[
            Literal["start", "pause", "resume", "cancel", "complete"],
            Field(
                description="start (or move the start of a running timer), pause, resume, "
                "cancel (discard without saving), complete (stop and save with amounts)."
            ),
        ],
        start_time: StartTime = None,
        end_time: Annotated[
            str | None,
            Field(description="complete only: when pumping ended, ISO 8601. Omit = now (or the pause time if paused)."),
        ] = None,
        left_amount: Annotated[
            float | None, Field(description="complete only: left side amount; give with right_amount.", ge=0)
        ] = None,
        right_amount: Annotated[
            float | None, Field(description="complete only: right side amount; give with left_amount.", ge=0)
        ] = None,
        total_amount: Annotated[
            float | None,
            Field(description="complete only: total amount when sides were not measured separately.", ge=0),
        ] = None,
        units: Annotated[VolumeUnits | None, Field(description="complete only: volume units.")] = None,
        notes: Notes = None,
        child: Child = None,
    ) -> str:
        """Control the live breast-pumping timer. Use log_pump for a session that is already over."""
        cid = await resolve(child)
        api_ = hb()
        _only(action, {"start_time"} if action in ("start", "complete") else set(), start_time=start_time)
        _only(
            action,
            {"end_time", "left_amount", "right_amount", "total_amount", "units", "notes"}
            if action == "complete"
            else set(),
            end_time=end_time,
            left_amount=left_amount,
            right_amount=right_amount,
            total_amount=total_amount,
            units=units,
            notes=notes,
        )
        await require_running(cid, "pump", action)
        if action == "start":
            await api_.start_pump(cid, start_time=clock.maybe(start_time))
        elif action == "pause":
            await api_.pause_pump(cid)
        elif action == "resume":
            await api_.resume_pump(cid)
        elif action == "cancel":
            await api_.cancel_pump(cid)
        else:
            await api_.complete_pump(
                cid,
                start_time=clock.maybe(start_time),
                end_time=clock.maybe(end_time),
                left_amount=left_amount,
                right_amount=right_amount,
                total_amount=total_amount,
                units=units,
                notes=notes,
            )
        return f"Pump timer: {action} done"

    @mcp.tool
    async def log_pump(
        start_time: StartTime = None,
        duration: Seconds = None,
        left_amount: Annotated[
            float | None, Field(description="Left side amount; give with right_amount.", ge=0)
        ] = None,
        right_amount: Annotated[
            float | None, Field(description="Right side amount; give with left_amount.", ge=0)
        ] = None,
        total_amount: Annotated[
            float | None, Field(description="Total amount when sides were not measured separately.", ge=0)
        ] = None,
        units: Annotated[VolumeUnits, Field(description="Volume units.")] = "ml",
        notes: Notes = None,
        child: Child = None,
    ) -> str:
        """Save a breast-pumping session that already happened. Give either left_amount + right_amount or
        total_amount."""
        cid = await resolve(child)
        start = clock.parse(start_time)
        await hb().log_pump(
            cid,
            start_time=start,
            duration=duration,
            left_amount=left_amount,
            right_amount=right_amount,
            total_amount=total_amount,
            units=units,
            notes=notes,
        )
        return f"Logged pump session at {start.isoformat(timespec='minutes')}"

    # --- activities -------------------------------------------------------------------------------

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def activity_timer(
        action: Annotated[
            Literal["start", "pause", "resume", "switch", "cancel", "complete"],
            Field(
                description="start (or move the start of a running timer), pause, resume, switch (relabel "
                "the running timer as `mode`), cancel (discard without saving), complete (stop and save)."
            ),
        ],
        mode: Annotated[ActivityMode, Field(description="Which activity. For switch, the new activity.")],
        start_time: StartTime = None,
        end_time: Annotated[
            str | None, Field(description="complete only: when it ended, ISO 8601. Cannot be combined with duration.")
        ] = None,
        duration: Annotated[
            float | None, Field(description="complete only: duration override in seconds.", ge=0)
        ] = None,
        notes: Notes = None,
        child: Child = None,
    ) -> str:
        """Control a live activity timer (bath, tummy time, story time, play, etc.). Use log_activity for an
        activity that is already over."""
        cid = await resolve(child)
        api_ = hb()
        _only(action, {"start_time"} if action in ("start", "complete") else set(), start_time=start_time)
        _only(
            action,
            {"end_time", "duration", "notes"} if action == "complete" else set(),
            end_time=end_time,
            duration=duration,
            notes=notes,
        )
        await require_running(cid, "activities", action, mode)
        if action == "start":
            await api_.start_activity(cid, mode, start_time=clock.maybe(start_time))
        elif action == "pause":
            await api_.pause_activity(cid, mode)
        elif action == "resume":
            await api_.resume_activity(cid, mode)
        elif action == "switch":
            await api_.switch_activity(cid, mode)
        elif action == "cancel":
            await api_.cancel_activity(cid, mode)
        else:
            await api_.complete_activity(
                cid,
                mode,
                start_time=clock.maybe(start_time),
                end_time=clock.maybe(end_time),
                duration=duration,
                notes=notes,
            )
        return f"{mode} timer: {action} done"

    @mcp.tool
    async def log_activity(
        mode: Annotated[ActivityMode, Field(description="Which activity.")],
        start_time: StartTime = None,
        duration: Seconds = None,
        notes: Notes = None,
        child: Child = None,
    ) -> str:
        """Save an activity (bath, tummy time, story time, play, etc.) that already happened."""
        cid = await resolve(child)
        start = clock.parse(start_time)
        await hb().log_activity(cid, mode=mode, start_time=start, duration=duration, notes=notes)
        return f"Logged {mode} at {start.isoformat(timespec='minutes')}"

    # --- solids -----------------------------------------------------------------------------------

    @mcp.tool(annotations=_READ)
    async def list_foods(
        query: Annotated[
            str | None,
            Field(description="Part of the food name, e.g. 'avoc'. Omit to list only the family's custom foods."),
        ] = None,
        include_archived: Annotated[bool, Field(description="Also list archived custom foods.")] = False,
        child: Child = None,
    ) -> list[dict[str, Any]]:
        """Find solid foods (Huckleberry's catalog plus the family's custom foods) to use in log_solids.
        Returns id, source and name for each match (at most 50)."""
        cid = await resolve(child)
        foods: list[Any] = [f for f in await hb().list_solids_custom_foods(cid, include_archived=include_archived)]
        if query:
            needle = query.strip().lower()
            foods = [
                f
                for f in [*foods, *await hb().list_solids_curated_foods()]
                if needle in f.name.lower() or any(needle in a.lower() for a in getattr(f, "aka", None) or [])
            ]
        return [{"id": f.id, "source": f.source, "name": f.name} for f in foods[:50]]

    @mcp.tool
    async def create_custom_food(
        name: Annotated[str, Field(description="Food name as the family calls it.")],
        image: Annotated[str, Field(description="Firebase Storage image filename. Normally left empty.")] = "",
        child: Child = None,
    ) -> dict[str, Any]:
        """Add a custom solid food when list_foods finds nothing suitable. Returns the new food's id."""
        cid = await resolve(child)
        return _dump(await hb().create_solids_custom_food(cid, name, image=image))

    @mcp.tool
    async def log_solids(
        foods: Annotated[
            list[SolidsFoodReference],
            Field(
                description="Foods eaten: id, source and name from "
                "list_foods, plus amount as free text (e.g. "
                "'2 spoons').",
                min_length=1,
            ),
        ],
        start_time: StartTime = None,
        reaction: Annotated[SolidsReaction | None, Field(description="How the child liked it.")] = None,
        notes: Annotated[str, Field(description="Optional free-text note.")] = "",
        food_note_image: Annotated[
            str | None, Field(description="Existing Firebase Storage image filename; usually omit.")
        ] = None,
        child: Child = None,
    ) -> str:
        """Log a solid-food meal. Look foods up with list_foods first (or create_custom_food)."""
        cid = await resolve(child)
        start = clock.parse(start_time)
        await hb().log_solids(
            cid, start_time=start, foods=foods, notes=notes, reaction=reaction, food_note_image=food_note_image
        )
        return f"Logged solids ({', '.join(f.name for f in foods)}) at {start.isoformat(timespec='minutes')}"

    # --- diaper -----------------------------------------------------------------------------------

    @mcp.tool
    async def log_diaper(
        mode: Annotated[DiaperMode, Field(description="What was in the diaper.")],
        start_time: StartTime = None,
        pee_amount: Annotated[DiaperAmount | None, Field(description="How much pee.")] = None,
        poo_amount: Annotated[DiaperAmount | None, Field(description="How much poo.")] = None,
        color: Annotated[PooColor | None, Field(description="Poo color.")] = None,
        consistency: Annotated[PooConsistency | None, Field(description="Poo consistency.")] = None,
        diaper_rash: Annotated[bool, Field(description="Whether there is diaper rash.")] = False,
        notes: Notes = None,
        child: Child = None,
    ) -> str:
        """Log a diaper change."""
        cid = await resolve(child)
        start = clock.parse(start_time)
        await hb().log_diaper(
            cid,
            start_time=start,
            mode=mode,
            pee_amount=pee_amount,
            poo_amount=poo_amount,
            color=color,
            consistency=consistency,
            diaper_rash=diaper_rash,
            notes=notes,
        )
        return f"Logged {mode} diaper at {start.isoformat(timespec='minutes')}"

    @mcp.tool
    async def log_potty(
        mode: Annotated[DiaperMode, Field(description="What happened: pee, poo, both, or dry.")],
        how_it_happened: Annotated[PottyResult, Field(description="satButDry, wentPotty (success), or accident.")],
        start_time: StartTime = None,
        pee_amount: Annotated[DiaperAmount | None, Field(description="How much pee.")] = None,
        poo_amount: Annotated[DiaperAmount | None, Field(description="How much poo.")] = None,
        color: Annotated[PooColor | None, Field(description="Poo color.")] = None,
        consistency: Annotated[PooConsistency | None, Field(description="Poo consistency.")] = None,
        notes: Notes = None,
        child: Child = None,
    ) -> str:
        """Log a potty-training attempt (toilet/potty, not a diaper change)."""
        cid = await resolve(child)
        start = clock.parse(start_time)
        await hb().log_potty(
            cid,
            start_time=start,
            mode=mode,
            how_it_happened=how_it_happened,
            pee_amount=pee_amount,
            poo_amount=poo_amount,
            color=color,
            consistency=consistency,
            notes=notes,
        )
        return f"Logged potty ({how_it_happened}) at {start.isoformat(timespec='minutes')}"

    # --- health -----------------------------------------------------------------------------------

    @mcp.tool
    async def log_growth(
        weight: Annotated[float | None, Field(description="Weight: kg (metric) or lbs (imperial).", gt=0)] = None,
        height: Annotated[float | None, Field(description="Height: cm (metric) or inches (imperial).", gt=0)] = None,
        head: Annotated[
            float | None, Field(description="Head circumference: cm (metric) or inches (imperial).", gt=0)
        ] = None,
        units: Annotated[GrowthUnits, Field(description="Unit system for all three measurements.")] = "metric",
        start_time: StartTime = None,
        child: Child = None,
    ) -> str:
        """Log growth measurements (any of weight, height, head circumference)."""
        cid = await resolve(child)
        start = clock.parse(start_time)
        await hb().log_growth(cid, start_time=start, weight=weight, height=height, head=head, units=units)
        return f"Logged growth at {start.isoformat(timespec='minutes')}"

    @mcp.tool
    async def log_temperature(
        amount: Annotated[float, Field(description="Temperature reading.")],
        units: Annotated[TemperatureUnits, Field(description="C for Celsius, F for Fahrenheit.")],
        start_time: StartTime = None,
        notes: Notes = None,
        child: Child = None,
    ) -> str:
        """Log a body temperature reading."""
        cid = await resolve(child)
        start = clock.parse(start_time)
        await hb().log_temperature(cid, start_time=start, amount=amount, units=units, notes=notes)
        return f"Logged {amount:g} {units} at {start.isoformat(timespec='minutes')}"

    @mcp.tool(annotations=_READ)
    async def list_medicine_types(child: Child = None) -> list[dict[str, Any]]:
        """List the medicines set up for this child (names usable in log_medicine)."""
        cid = await resolve(child)
        return [_dump(m) for m in await hb().list_medicine_types(cid)]

    @mcp.tool
    async def create_medicine_type(
        name: Annotated[str, Field(description="Medicine name, e.g. 'Vitamin D'.")],
        child: Child = None,
    ) -> dict[str, Any]:
        """Add a medicine to this child's list so it can be logged. Check list_medicine_types first."""
        cid = await resolve(child)
        return _dump(await hb().create_medicine_type(cid, name))

    @mcp.tool
    async def log_medicine(
        name: Annotated[str, Field(description="Medicine name as shown by list_medicine_types (case-insensitive).")],
        amount: Annotated[float | None, Field(description="Dose amount, in `units`.", ge=0)] = None,
        units: Annotated[MedicationUnits | None, Field(description="Dose units.")] = None,
        start_time: StartTime = None,
        notes: Annotated[str, Field(description="Optional free-text note.")] = "",
        child: Child = None,
    ) -> str:
        """Log a medicine dose by medicine name. The medicine must exist (see create_medicine_type)."""
        cid = await resolve(child)
        types = await hb().list_medicine_types(cid)
        match = [m for m in types if m.name.strip().lower() == name.strip().lower()]
        if not match:
            known = ", ".join(m.name for m in types) or "none"
            raise ToolError(f"No medicine named {name!r}. Known: {known}. Use create_medicine_type to add it.")
        start = clock.parse(start_time)
        await hb().log_medicine(cid, start_time=start, medicine_type=match[0], amount=amount, units=units, notes=notes)
        return f"Logged {match[0].name} at {start.isoformat(timespec='minutes')}"

    # --- history ----------------------------------------------------------------------------------

    @mcp.tool(annotations=_READ)
    async def list_entries(
        tracker: TrackerArg,
        start: Annotated[str | None, Field(description="Range start, ISO 8601. Omit = 24 hours before end.")] = None,
        end: Annotated[str | None, Field(description="Range end, ISO 8601. Omit = now.")] = None,
        child: Child = None,
    ) -> list[dict[str, Any]]:
        """List saved entries of one tracker in a time range, oldest first. Each has an id (for update_entry /
        delete_entry), start_local/end_local in the server timezone, and the raw Firebase fields in data."""
        cid = await resolve(child)
        end_dt = clock.parse(end)
        start_dt = clock.parse(start) if start is not None else end_dt - timedelta(hours=24)
        return [entry_out(e) for e in await hb().list_entries(cid, tracker, start_dt, end_dt)]

    @mcp.tool
    async def update_entry(
        tracker: TrackerArg,
        entry_id: Annotated[str, Field(description="Entry id from list_entries.")],
        changes: Annotated[
            dict[str, JsonValue],
            Field(
                description="Fields to change, using the Firebase field "
                "names shown in list_entries data (times are epoch "
                "seconds). null removes a field."
            ),
        ],
        child: Child = None,
    ) -> dict[str, Any]:
        """Edit a saved entry. Find it with list_entries first and confirm with the user. Returns the updated
        entry."""
        cid = await resolve(child)
        return entry_out(await hb().update_entry(cid, tracker, entry_id, changes))

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def delete_entry(
        tracker: TrackerArg,
        entry_id: Annotated[str, Field(description="Entry id from list_entries.")],
        child: Child = None,
    ) -> str:
        """Permanently delete a saved entry. Find it with list_entries and confirm with the user first."""
        cid = await resolve(child)
        await hb().delete_entry(cid, tracker, entry_id)
        return f"Deleted {tracker} entry {entry_id}"

    return mcp


def _auth_from_env() -> tuple[AuthProvider | None, frozenset[str]]:
    """Build HTTP auth from the environment, refusing to run unauthenticated unless explicitly allowed.

    Returns the auth provider (None only with ``HUCKLEBERRY_MCP_NO_AUTH=1``) and the email allowlist.
    """
    # The connector URL is the origin plus /mcp; tolerate a base URL pasted with that path.
    base_url = os.environ.get("HUCKLEBERRY_MCP_BASE_URL", "").rstrip("/").removesuffix("/mcp")
    allowed = frozenset(
        e.strip().lower() for e in os.environ.get("HUCKLEBERRY_MCP_ALLOWED_EMAILS", "").split(",") if e.strip()
    )
    clerk = [os.environ.get(k) for k in ("CLERK_DOMAIN", "CLERK_OAUTH_CLIENT_ID", "CLERK_OAUTH_CLIENT_SECRET")]
    if all(clerk) and base_url:
        domain, client_id, client_secret = clerk
        assert domain and client_id and client_secret
        if not allowed:
            raise SystemExit(
                "Refusing to start: set HUCKLEBERRY_MCP_ALLOWED_EMAILS to the emails allowed to use the server."
            )
        provider = ClerkProvider(domain=domain, client_id=client_id, client_secret=client_secret, base_url=base_url)
        return provider, allowed
    if os.environ.get("HUCKLEBERRY_MCP_NO_AUTH") == "1":
        _LOGGER.warning("HUCKLEBERRY_MCP_NO_AUTH=1: serving without authentication")
        return None, allowed
    raise SystemExit(
        "Refusing to start: set CLERK_DOMAIN, CLERK_OAUTH_CLIENT_ID, CLERK_OAUTH_CLIENT_SECRET and "
        "HUCKLEBERRY_MCP_BASE_URL, or HUCKLEBERRY_MCP_NO_AUTH=1 to serve without authentication."
    )


def load_env_file(path: str = ".config/.env") -> None:
    """Load ``KEY=VALUE`` lines from the local config file. Variables already set win; blank values are skipped."""
    file = Path(path)
    if not file.is_file():
        return
    for line in file.read_text().splitlines():
        key, _, value = line.strip().removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and value and not key.startswith("#"):
            os.environ.setdefault(key, value)


def main() -> None:
    """Console entry point: ``huckleberry-mcp [--stdio]``."""
    parser = argparse.ArgumentParser(description="Huckleberry MCP server")
    parser.add_argument("--stdio", action="store_true", help="serve over stdio for local MCP clients")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    load_env_file()
    missing = [
        k for k in ("HUCKLEBERRY_EMAIL", "HUCKLEBERRY_PASSWORD", "HUCKLEBERRY_TIMEZONE") if not os.environ.get(k)
    ]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")
    if args.stdio:
        create_server().run(transport="stdio")
        return
    auth, allowed = _auth_from_env()
    # FastMCP's host/origin protection stays at its default (off): Claude calls from its own
    # origin through the tunnel, and OAuth is the gate.
    create_server(auth=auth, allowed_emails=allowed).run(
        transport="http",
        host=os.environ.get("HUCKLEBERRY_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("HUCKLEBERRY_MCP_PORT", "8765")),
        path="/mcp",
        # Tools keep no per-session state; stateless avoids idle session expiry and survives restarts.
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
