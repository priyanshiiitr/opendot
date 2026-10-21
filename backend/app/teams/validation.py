"""Backend plan validation — model proposals are not trusted until this passes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PlanValidationError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class PlanLimits:
    max_subtasks: int = 12
    max_depth: int = 1
    max_total_runs: int = 20
    max_concurrent: int = 2
    max_retries: int = 1
    allowed_member_ids: frozenset[int] = frozenset()
    lead_teammate_id: int | None = None


def _detect_cycle(keys: set[str], deps: dict[str, list[str]]) -> str | None:
    visiting: set[str] = set()
    done: set[str] = set()

    def dfs(node: str) -> bool:
        if node in done:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        for d in deps.get(node, []):
            if d in keys and dfs(d):
                return True
        visiting.remove(node)
        done.add(node)
        return False

    for k in keys:
        if dfs(k):
            return k
    return None


def validate_plan(subtasks: list[dict[str, Any]], limits: PlanLimits) -> list[dict[str, Any]]:
    if not subtasks:
        raise PlanValidationError("Plan requires at least one subtask.")
    if len(subtasks) > limits.max_subtasks:
        raise PlanValidationError(f"Plan exceeds max_subtasks ({limits.max_subtasks}).")
    if len(subtasks) > limits.max_total_runs:
        raise PlanValidationError(f"Plan exceeds max_total_runs ({limits.max_total_runs}).")

    keys: set[str] = set()
    normalized: list[dict[str, Any]] = []
    deps_map: dict[str, list[str]] = {}

    for raw in subtasks:
        key = str(raw.get("subtask_key") or "").strip()
        if not key or key in keys:
            raise PlanValidationError("Each subtask needs a unique subtask_key.")
        keys.add(key)
        assignee = raw.get("assignee_teammate_id")
        try:
            assignee_id = int(assignee)
        except (TypeError, ValueError) as exc:
            raise PlanValidationError(f"Subtask {key} has invalid assignee_teammate_id.") from exc
        if limits.allowed_member_ids and assignee_id not in limits.allowed_member_ids:
            raise PlanValidationError(f"Assignee {assignee_id} is not an authorized team member.")
        depth = int(raw.get("depth") or 1)
        if depth > limits.max_depth:
            raise PlanValidationError(f"Subtask {key} exceeds max_depth ({limits.max_depth}).")
        if depth < 1:
            raise PlanValidationError(f"Subtask {key} has invalid depth.")
        # Specialists at depth>=1 cannot nest further when max_depth==1
        depends_on = [str(d) for d in (raw.get("depends_on") or [])]
        deps_map[key] = depends_on
        objective = str(raw.get("objective") or "").strip()
        if not objective:
            raise PlanValidationError(f"Subtask {key} requires an objective.")
        cats = list(raw.get("allowed_tool_categories") or ["local", "workspace"])
        # Never allow a plan to invent telegram/connector expansion by default.
        forbidden = {"unknown"}
        if any(c in forbidden for c in cats):
            raise PlanValidationError(f"Subtask {key} has invalid tool categories.")
        normalized.append(
            {
                "subtask_key": key,
                "assignee_teammate_id": assignee_id,
                "objective": objective,
                "output_format": str(raw.get("output_format") or "structured_findings"),
                "depends_on": depends_on,
                "depth": depth,
                "critical": bool(raw.get("critical", True)),
                "allowed_tool_categories": cats,
            }
        )

    for key, deps in deps_map.items():
        for d in deps:
            if d not in keys:
                raise PlanValidationError(f"Unknown dependency '{d}' referenced by {key}.")
    cycle_at = _detect_cycle(keys, deps_map)
    if cycle_at:
        raise PlanValidationError(f"Dependency cycle detected at '{cycle_at}'.")
    if limits.max_concurrent < 1:
        raise PlanValidationError("max_concurrent must be >= 1.")
    return normalized
