"""Billable cloud provider stub — refuses to provision without explicit approval + credentials."""

from __future__ import annotations

from typing import Any


class BillableCloudNotConfigured(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class BillableCloudStub:
    """Placeholder for Fly/AWS/GCP/etc. Never creates billable resources from this codebase path."""

    provider_name = "billable_cloud"
    isolation_tier = "managed_cloud"

    def __init__(self, provider_id: str = "unset"):
        self.provider_id = provider_id

    async def provision(self) -> dict[str, Any]:
        raise BillableCloudNotConfigured(
            "Billable cloud computers are not auto-provisioned. "
            "Choose a provider (see docs/decisions/0012), set credentials outside chat, "
            "and give explicit approval before any paid VM is created."
        )

    async def start(self, *, headed: bool = False) -> dict[str, Any]:
        _ = headed
        raise BillableCloudNotConfigured("Billable cloud not configured.")

    async def stop(self) -> dict[str, Any]:
        raise BillableCloudNotConfigured("Billable cloud not configured.")

    async def health(self) -> dict[str, Any]:
        return {"healthy": False, "configured": False, "provider": self.provider_id}

    async def open_browser(self, *, headed: bool = False) -> dict[str, Any]:
        _ = headed
        raise BillableCloudNotConfigured("Billable cloud not configured.")

    async def close_browser(self) -> dict[str, Any]:
        raise BillableCloudNotConfigured("Billable cloud not configured.")

    async def status(self) -> dict[str, Any]:
        return {"provider": self.provider_name, "configured": False}

    async def preview(self) -> bytes:
        raise BillableCloudNotConfigured("Billable cloud not configured.")

    async def enter_human_control(self) -> dict[str, Any]:
        raise BillableCloudNotConfigured("Billable cloud not configured.")

    async def release_human_control(self) -> dict[str, Any]:
        raise BillableCloudNotConfigured("Billable cloud not configured.")

    async def delete(self, *, confirm: bool = False) -> dict[str, Any]:
        _ = confirm
        raise BillableCloudNotConfigured("Billable cloud not configured.")

    def workspace_paths(self) -> dict[str, str]:
        return {}
