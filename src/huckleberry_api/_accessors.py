"""Mixin that exposes child-scoped accessors on the API client."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .accessors import ChildHandle

if TYPE_CHECKING:
    from .api import HuckleberryAPI


class AccessorMixin:
    """Entry point for the child-scoped API."""

    def child(self, child_uid: str) -> ChildHandle:
        """Bind a child and return its tracker handles.

        Performs no I/O: the handle only stores the child uid and the client.
        """
        return ChildHandle(cast("HuckleberryAPI", self), child_uid)
