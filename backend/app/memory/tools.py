"""Memory tools — model proposes; MemoryService enforces policy."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from ..computers.context import current_teammate_id
from ..tools import ToolError, ToolRegistry, ToolSpec
from .service import MemoryError, MemoryService


class MemorySaveArguments(BaseModel):
    content: str = Field(min_length=1, max_length=4_000)
    memory_type: Literal["preference", "teammate", "project", "task", "fact"] = "preference"
    scope: Literal["user", "teammate", "project", "task"] = "user"
    scope_id: str = Field(default="default", max_length=120)
    source: Literal["explicit", "inferred"] = "explicit"
    tags: list[str] = Field(default_factory=list, max_length=10)


class MemoryListArguments(BaseModel):
    scope: Literal["user", "teammate", "project", "task"] | None = None
    scope_id: str | None = None
    memory_type: str | None = None


class MemoryDeleteArguments(BaseModel):
    memory_id: str = Field(min_length=8, max_length=36)


def register_memory_tools(registry: ToolRegistry, session_factory: sessionmaker, service: MemoryService, timeout: float) -> None:
    async def memory_save(arguments: MemorySaveArguments) -> dict[str, Any]:
        scope_id = arguments.scope_id
        if arguments.scope == "teammate":
            tid = current_teammate_id.get()
            if tid is None:
                raise ToolError("No teammate context for teammate-scoped memory.")
            scope_id = str(tid)
        try:
            with session_factory() as db:
                row = service.create(
                    db,
                    content=arguments.content,
                    memory_type=arguments.memory_type,
                    scope=arguments.scope,
                    scope_id=scope_id,
                    source=arguments.source,
                    tags=arguments.tags,
                )
                return {"action": "memory_save", "memory": MemoryService.public(row)}
        except MemoryError as exc:
            raise ToolError(exc.reason) from exc

    async def memory_list(arguments: MemoryListArguments) -> dict[str, Any]:
        with session_factory() as db:
            rows = service.list(
                db,
                scope=arguments.scope,
                scope_id=arguments.scope_id,
                memory_type=arguments.memory_type,
            )
            return {"action": "memory_list", "memories": [MemoryService.public(r) for r in rows]}

    async def memory_delete(arguments: MemoryDeleteArguments) -> dict[str, Any]:
        try:
            with session_factory() as db:
                row = service.delete(db, arguments.memory_id)
                return {"action": "memory_delete", "id": row.id, "status": row.status}
        except MemoryError as exc:
            raise ToolError(exc.reason) from exc

    registry.register(
        ToolSpec(
            "memory_save",
            "Save an explicit user preference or scoped fact. Never store secrets. Inferred memories need source=inferred.",
            MemorySaveArguments,
            "approval_required",
            timeout,
            memory_save,
        )
    )
    registry.register(
        ToolSpec(
            "memory_list",
            "List active memories for a scope (does not return deleted memories).",
            MemoryListArguments,
            "read_only",
            timeout,
            memory_list,
        )
    )
    registry.register(
        ToolSpec(
            "memory_delete",
            "Soft-delete a memory so it cannot be retrieved again.",
            MemoryDeleteArguments,
            "approval_required",
            timeout,
            memory_delete,
        )
    )
