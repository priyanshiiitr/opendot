"""Credit-funded cloud execution policy (separate from paid billable stub)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from ..database import ComputerRecord

# Approximate list prices for demos — NOT a billing API guarantee.
# Refuse to claim exact spend caps without Cloud Billing budget APIs.
MACHINE_COST_ESTIMATES_USD_PER_HOUR: dict[str, float] = {
    "e2-micro": 0.008,
    "e2-small": 0.016,
}

ACTIVE_GCP_STATES = frozenset(
    {
        "provisioning",
        "starting",
        "ready",
        "busy",
        "paused",
        "waiting_human",
        "human_control",
        "recovering",
    }
)


class CreditPolicyError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class CostEstimate:
    provider: str
    machine_type: str
    zone: str
    usd_per_hour_approx: float
    idle_stop_minutes: int
    max_runtime_minutes: int
    max_concurrent_vms: int
    disk_gb: int
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "machine_type": self.machine_type,
            "zone": self.zone,
            "usd_per_hour_approx": self.usd_per_hour_approx,
            "idle_stop_minutes": self.idle_stop_minutes,
            "max_runtime_minutes": self.max_runtime_minutes,
            "max_concurrent_vms": self.max_concurrent_vms,
            "disk_gb": self.disk_gb,
            "currency": "USD",
            "is_guarantee": False,
            "notes": self.notes,
        }


def allowed_machine_types(settings: Settings) -> set[str]:
    raw = (settings.gcp_allowed_machine_types or "e2-micro,e2-small").strip()
    return {part.strip() for part in raw.split(",") if part.strip()}


def build_cost_estimate(settings: Settings) -> CostEstimate:
    machine = (settings.gcp_machine_type or "e2-micro").strip()
    allowed = allowed_machine_types(settings)
    if machine not in allowed:
        raise CreditPolicyError(
            f"Machine type '{machine}' is not allowlisted. Allowed: {sorted(allowed)}"
        )
    rate = MACHINE_COST_ESTIMATES_USD_PER_HOUR.get(machine)
    if rate is None:
        raise CreditPolicyError(
            f"No trusted cost estimate for machine type '{machine}'. Refusing provision."
        )
    notes = [
        "Estimate only — Google Cloud list-price approximation for Compute Engine vCPU/RAM.",
        "Persistent disk and network egress can add cost while the VM exists.",
        "Free-trial / promotional credits are not a hard spending cap; billing alerts are not caps.",
        "This path never enables pay-as-you-go upgrades. Out-of-pocket spend is not authorized.",
        f"Idle stop target: {settings.computer_idle_stop_minutes} minutes; "
        f"max runtime: {settings.computer_max_runtime_minutes} minutes.",
    ]
    return CostEstimate(
        provider="gcp_compute",
        machine_type=machine,
        zone=(settings.gcp_zone or "us-central1-a").strip(),
        usd_per_hour_approx=rate,
        idle_stop_minutes=settings.computer_idle_stop_minutes,
        max_runtime_minutes=settings.computer_max_runtime_minutes,
        max_concurrent_vms=settings.gcp_max_concurrent_vms,
        disk_gb=settings.gcp_boot_disk_gb,
        notes=notes,
    )


def count_active_gcp_computers(db: Session, *, exclude_computer_id: str | None = None) -> int:
    q = db.query(ComputerRecord).filter(
        ComputerRecord.provider == "gcp_compute",
        ComputerRecord.state.in_(tuple(ACTIVE_GCP_STATES)),
    )
    if exclude_computer_id:
        q = q.filter(ComputerRecord.id != exclude_computer_id)
    return q.count()


def assert_credit_funded_gcp_allowed(
    settings: Settings,
    db: Session,
    *,
    exclude_computer_id: str | None = None,
) -> CostEstimate:
    """Fail closed before any GCP mutate call."""
    if settings.computer_allow_billable:
        raise CreditPolicyError(
            "AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE is true, but out-of-pocket billable "
            "provisioning is not authorized. Keep it false and use credit-funded mode only."
        )
    if not settings.computer_credit_funded_enabled:
        raise CreditPolicyError(
            "Credit-funded GCP mode is disabled. Set AI_TEAMMATES_COMPUTER_CREDIT_FUNDED_ENABLED=true "
            "only after configuring free-trial/credits and GCP credentials outside chat."
        )
    if not (settings.gcp_project_id or "").strip():
        raise CreditPolicyError(
            "GCP project id missing. Set AI_TEAMMATES_GCP_PROJECT_ID after creating a project "
            "on an eligible free-trial or credit account."
        )
    if not (settings.gcp_zone or "").strip():
        raise CreditPolicyError("AI_TEAMMATES_GCP_ZONE is required for gcp_compute.")
    estimate = build_cost_estimate(settings)
    if settings.gcp_max_concurrent_vms < 1:
        raise CreditPolicyError("gcp_max_concurrent_vms must be >= 1 when credit-funded mode is on.")
    active = count_active_gcp_computers(db, exclude_computer_id=exclude_computer_id)
    if active >= settings.gcp_max_concurrent_vms:
        raise CreditPolicyError(
            f"Concurrent GCP execution VM limit reached ({active}/{settings.gcp_max_concurrent_vms}). "
            "Stop or delete an existing gcp_compute computer before provisioning another."
        )
    return estimate
