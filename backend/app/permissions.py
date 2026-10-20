from dataclasses import dataclass
from typing import Literal

from .tools import ToolSpec


@dataclass(frozen=True)
class PermissionDecision:
    status: Literal["allowed", "approval_required", "denied"]
    reason: str


class PermissionBroker:
    """Backend enforcement; model text cannot alter this policy."""
    def assess(self, tool: ToolSpec) -> PermissionDecision:
        if tool.permission == "read_only":
            return PermissionDecision("allowed", "Read-only local tool")
        if tool.permission == "approval_required":
            return PermissionDecision("approval_required", "Explicit user approval is required")
        return PermissionDecision("denied", "Tool has no approved permission classification")
