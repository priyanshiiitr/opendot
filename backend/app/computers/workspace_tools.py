"""Workspace / artifact tools bound to the current teammate computer context."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..tools import ToolError, ToolRegistry, ToolSpec
from .context import current_teammate_id
from .manager import ComputerError, ComputerManager
from .workspace import WorkspaceError


class WorkspaceListArguments(BaseModel):
    area: Literal["workspace", "artifacts"] = "workspace"


class WorkspaceReadArguments(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    area: Literal["workspace", "artifacts"] = "workspace"


class WorkspaceWriteArguments(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=0, max_length=200_000)
    overwrite: bool = False


class WorkspaceWriteCsvArguments(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    overwrite: bool = False


def register_workspace_tools(registry: ToolRegistry, computers: ComputerManager, timeout_seconds: float) -> None:
    def _store():
        tid = current_teammate_id.get()
        if tid is None:
            raise ToolError("No teammate computer context for workspace tools.")
        return computers.workspace_for_teammate(tid)

    async def workspace_list(arguments: WorkspaceListArguments) -> dict[str, Any]:
        try:
            files = _store().list_files(area=arguments.area)
            return {"action": "workspace_list", "area": arguments.area, "files": files}
        except (WorkspaceError, ComputerError) as exc:
            raise ToolError(getattr(exc, "reason", str(exc))) from exc

    async def workspace_read(arguments: WorkspaceReadArguments) -> dict[str, Any]:
        try:
            data = _store().read_text(arguments.path, area=arguments.area)
            return {"action": "workspace_read", **data}
        except (WorkspaceError, ComputerError) as exc:
            raise ToolError(getattr(exc, "reason", str(exc))) from exc

    async def workspace_write(arguments: WorkspaceWriteArguments) -> dict[str, Any]:
        try:
            data = _store().write_text(arguments.path, arguments.content, overwrite=arguments.overwrite)
            return {"action": "workspace_write", **data}
        except (WorkspaceError, ComputerError) as exc:
            raise ToolError(getattr(exc, "reason", str(exc))) from exc

    async def workspace_write_csv(arguments: WorkspaceWriteCsvArguments) -> dict[str, Any]:
        try:
            data = _store().write_csv(arguments.path, arguments.rows, overwrite=arguments.overwrite)
            return {"action": "workspace_write_csv", **data}
        except (WorkspaceError, ComputerError) as exc:
            raise ToolError(getattr(exc, "reason", str(exc))) from exc

    registry.register(
        ToolSpec(
            "workspace_list",
            "List files in this teammate's authorized workspace or artifacts directory.",
            WorkspaceListArguments,
            "read_only",
            timeout_seconds,
            workspace_list,
        )
    )
    registry.register(
        ToolSpec(
            "workspace_read",
            "Read a text file from this teammate's workspace or artifacts (path-traversal safe).",
            WorkspaceReadArguments,
            "read_only",
            timeout_seconds,
            workspace_read,
        )
    )
    registry.register(
        ToolSpec(
            "workspace_write",
            "Create or update a Markdown/text/JSON/CSV file in the teammate workspace. Overwrite requires approval.",
            WorkspaceWriteArguments,
            "approval_required",
            timeout_seconds,
            workspace_write,
        )
    )
    registry.register(
        ToolSpec(
            "workspace_write_csv",
            "Write a CSV spreadsheet into the teammate workspace from structured rows. Overwrite requires approval.",
            WorkspaceWriteCsvArguments,
            "approval_required",
            timeout_seconds,
            workspace_write_csv,
        )
    )
