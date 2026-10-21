"""Checkpoint helpers and crash-recovery classification."""

from __future__ import annotations

import json
from typing import Any

# Tools with external side effects that must not be blindly replayed after an ambiguous crash.
CONSEQUENTIAL_TOOLS = frozenset(
    {
        "telegram_send_message",
        "browser_click",
        "browser_type",
        "browser_open",
    }
)


def load_checkpoint(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def dump_checkpoint(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def checkpoint_after_event(checkpoint: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Update checkpoint from a streamed runtime event."""
    next_cp = dict(checkpoint)
    etype = event.get("type")
    if etype == "execution":
        status = event.get("status")
        tool = event.get("tool_name")
        tool_call_id = event.get("tool_call_id")
        next_cp["last_event_type"] = event.get("event_type")
        next_cp["last_status"] = status
        next_cp["last_tool_name"] = tool
        next_cp["last_tool_call_id"] = tool_call_id
        if event.get("event_type") == "tool_result" and status == "completed":
            next_cp["last_completed_tool_call_id"] = tool_call_id
            next_cp["safe_to_resume"] = True
            next_cp["uncertain_side_effect"] = False
        elif event.get("event_type") == "tool_call" and status == "running" and tool in CONSEQUENTIAL_TOOLS:
            # In-flight consequential work: crash here is ambiguous.
            next_cp["safe_to_resume"] = False
            next_cp["uncertain_side_effect"] = True
        elif event.get("event_type") == "tool_call" and status in {"queued", "validated", "running"}:
            if tool not in CONSEQUENTIAL_TOOLS:
                next_cp["safe_to_resume"] = True
                next_cp["uncertain_side_effect"] = False
    elif etype == "approval_required":
        next_cp["pending_approval_id"] = event.get("approval_id")
        next_cp["safe_to_resume"] = True
        next_cp["uncertain_side_effect"] = False
    elif etype == "done":
        next_cp["safe_to_resume"] = True
        next_cp["uncertain_side_effect"] = False
        next_cp["completed"] = True
    return next_cp


def classify_interrupted_run(checkpoint: dict[str, Any], agent_status: str | None) -> str:
    """
    Return target AutomationRun status after process crash.

    - waiting_approval stays waiting_approval (never auto-approve)
    - uncertain consequential → needs_reconciliation
    - otherwise → interrupted (may be re-queued safely by operator / resume path)
    """
    if agent_status == "waiting_approval" or checkpoint.get("pending_approval_id"):
        return "waiting_approval"
    if checkpoint.get("uncertain_side_effect"):
        return "needs_reconciliation"
    return "interrupted"
