from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

from .database import ConnectorGrant
from .tools import ToolSpec

# Tool name → coarse category for skill allow-lists (skills cannot expand beyond this).
TOOL_CATEGORIES: dict[str, str] = {
    "get_current_time": "local",
    "calculate": "local",
    "demo_request_approval": "local",
    "browser_open": "browser",
    "browser_snapshot": "browser",
    "browser_extract": "browser",
    "browser_click": "browser",
    "browser_type": "browser",
    "browser_screenshot": "browser",
    "browser_close": "browser",
    "workspace_list": "workspace",
    "workspace_read": "workspace",
    "workspace_write": "workspace",
    "workspace_write_csv": "workspace",
    "desktop_screenshot": "desktop",
    "desktop_open_url": "desktop",
    "desktop_click": "desktop",
    "desktop_type": "desktop",
    "desktop_hotkey": "desktop",
    "memory_save": "memory",
    "memory_list": "memory",
    "memory_delete": "memory",
    "telegram_get_bot_info": "telegram",
    "telegram_get_recent_updates": "telegram",
    "telegram_send_message": "telegram",
}


@dataclass(frozen=True)
class PermissionDecision:
    status: Literal["allowed", "approval_required", "denied"]
    reason: str


@dataclass
class AuthContext:
    """Resolved authorization for a single task/tool invocation."""

    teammate_id: int | None = None
    connector_id: str | None = None
    allowed_operations: list[str] = field(default_factory=list)
    resource_scope: dict[str, Any] = field(default_factory=dict)
    skill_allowed_categories: list[str] | None = None
    connector_grant_present: bool = False
    policy_version: str = "m7-v1"


class PermissionBroker:
    """Backend enforcement; model text / skills / memory cannot alter this policy."""

    def assess(self, tool: ToolSpec, auth: AuthContext | None = None) -> PermissionDecision:
        auth = auth or AuthContext()
        category = TOOL_CATEGORIES.get(tool.name, "unknown")
        if category == "unknown":
            return PermissionDecision("denied", "Tool has no approved permission classification")

        # Skill category gate (skills may only restrict, never expand).
        if auth.skill_allowed_categories is not None and category not in auth.skill_allowed_categories:
            return PermissionDecision(
                "denied",
                f"Skill does not allow tool category '{category}' for {tool.name}.",
            )

        # If an explicit connector grant exists, enforce it. No grant ⇒ legacy connector rules.
        if category == "telegram" and auth.connector_grant_present:
            if tool.name not in auth.allowed_operations:
                return PermissionDecision("denied", f"Connector grant does not allow {tool.name}.")

        if tool.permission == "read_only":
            return PermissionDecision("allowed", "Read-only local tool")
        if tool.permission == "approval_required":
            return PermissionDecision("approval_required", "Explicit user approval is required")
        return PermissionDecision("denied", "Tool has no approved permission classification")


def load_auth_context(
    db: Session,
    *,
    teammate_id: int | None,
    connector_id: str | None = None,
    skill_allowed_categories: list[str] | None = None,
) -> AuthContext:
    ops: list[str] = []
    scope: dict[str, Any] = {}
    present = False
    if teammate_id is not None and connector_id:
        grant = (
            db.query(ConnectorGrant)
            .filter(
                ConnectorGrant.teammate_id == teammate_id,
                ConnectorGrant.connector_id == connector_id,
                ConnectorGrant.enabled.is_(True),
            )
            .first()
        )
        if grant:
            present = True
            ops = json.loads(grant.operations_json or "[]")
            scope = json.loads(grant.resource_scope_json or "{}")
    return AuthContext(
        teammate_id=teammate_id,
        connector_id=connector_id,
        allowed_operations=ops,
        resource_scope=scope,
        skill_allowed_categories=skill_allowed_categories,
        connector_grant_present=present,
    )
