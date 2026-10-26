"""GCP Compute Engine client adapter — real API or in-process fake for tests."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class GcpClientError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class GcpInstance:
    name: str
    zone: str
    machine_type: str
    status: str  # PROVISIONING | STAGING | RUNNING | STOPPING | TERMINATED | SUSPENDED
    labels: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)
    self_link: str = ""
    external_ip: str | None = None


@runtime_checkable
class GcpComputeClient(Protocol):
    def create_instance(
        self,
        *,
        project: str,
        zone: str,
        name: str,
        machine_type: str,
        disk_gb: int,
        labels: dict[str, str],
        metadata: dict[str, str],
        startup_script: str,
    ) -> GcpInstance: ...

    def get_instance(self, *, project: str, zone: str, name: str) -> GcpInstance: ...

    def start_instance(self, *, project: str, zone: str, name: str) -> GcpInstance: ...

    def stop_instance(self, *, project: str, zone: str, name: str) -> GcpInstance: ...

    def delete_instance(self, *, project: str, zone: str, name: str) -> None: ...

    def credentials_ready(self) -> bool: ...


class FakeGcpComputeClient:
    """Deterministic in-memory GCP for contract tests — never talks to Google."""

    def __init__(self):
        self._lock = threading.Lock()
        self._instances: dict[tuple[str, str, str], GcpInstance] = {}
        self.create_calls = 0
        self.delete_calls = 0
        self.fail_create: str | None = None
        self.credentials_ok = True

    def credentials_ready(self) -> bool:
        return self.credentials_ok

    def create_instance(
        self,
        *,
        project: str,
        zone: str,
        name: str,
        machine_type: str,
        disk_gb: int,
        labels: dict[str, str],
        metadata: dict[str, str],
        startup_script: str,
    ) -> GcpInstance:
        _ = disk_gb, startup_script
        with self._lock:
            self.create_calls += 1
            if self.fail_create:
                raise GcpClientError(self.fail_create)
            key = (project, zone, name)
            if key in self._instances:
                raise GcpClientError(f"Instance already exists: {name}")
            inst = GcpInstance(
                name=name,
                zone=zone,
                machine_type=machine_type,
                status="RUNNING",
                labels=dict(labels),
                metadata=dict(metadata),
                self_link=f"fake://{project}/{zone}/{name}",
                external_ip=None,
            )
            self._instances[key] = inst
            return inst

    def get_instance(self, *, project: str, zone: str, name: str) -> GcpInstance:
        with self._lock:
            inst = self._instances.get((project, zone, name))
            if not inst:
                raise GcpClientError(f"Instance not found: {name}")
            return inst

    def start_instance(self, *, project: str, zone: str, name: str) -> GcpInstance:
        with self._lock:
            inst = self._instances.get((project, zone, name))
            if not inst:
                raise GcpClientError(f"Instance not found: {name}")
            inst.status = "RUNNING"
            return inst

    def stop_instance(self, *, project: str, zone: str, name: str) -> GcpInstance:
        with self._lock:
            inst = self._instances.get((project, zone, name))
            if not inst:
                raise GcpClientError(f"Instance not found: {name}")
            inst.status = "TERMINATED"
            return inst

    def delete_instance(self, *, project: str, zone: str, name: str) -> None:
        with self._lock:
            self.delete_calls += 1
            key = (project, zone, name)
            if key not in self._instances:
                raise GcpClientError(f"Instance not found: {name}")
            del self._instances[key]


class RealGcpComputeClient:
    """google-cloud-compute adapter. Requires ADC / service account outside chat."""

    def __init__(self):
        try:
            from google.cloud import compute_v1  # type: ignore
        except ImportError as exc:
            raise GcpClientError(
                "google-cloud-compute is not installed. "
                "pip install google-cloud-compute in the backend environment."
            ) from exc
        self._compute_v1 = compute_v1
        self._instances = compute_v1.InstancesClient()
        self._images = compute_v1.ImagesClient()

    def credentials_ready(self) -> bool:
        try:
            import google.auth  # type: ignore

            creds, _project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/compute"]
            )
            return creds is not None
        except Exception:
            return False

    def _wait(self, operation: Any, *, timeout: int = 300) -> None:
        result = operation.result(timeout=timeout)
        if getattr(operation, "error_code", None):
            raise GcpClientError(
                f"GCP operation failed: {operation.error_code} {operation.error_message}"
            )
        _ = result

    def _machine_type_url(self, zone: str, machine_type: str) -> str:
        return f"zones/{zone}/machineTypes/{machine_type}"

    def _debian_image(self, project: str) -> str:
        # Public Debian image family — stable for demos.
        image = self._images.get_from_family(project="debian-cloud", family="debian-12")
        return image.self_link

    def create_instance(
        self,
        *,
        project: str,
        zone: str,
        name: str,
        machine_type: str,
        disk_gb: int,
        labels: dict[str, str],
        metadata: dict[str, str],
        startup_script: str,
    ) -> GcpInstance:
        compute_v1 = self._compute_v1
        disk = compute_v1.AttachedDisk()
        disk.auto_delete = True
        disk.boot = True
        initialize = compute_v1.AttachedDiskInitializeParams()
        initialize.source_image = self._debian_image(project)
        initialize.disk_size_gb = disk_gb
        initialize.disk_type = f"zones/{zone}/diskTypes/pd-balanced"
        disk.initialize_params = initialize

        network_interface = compute_v1.NetworkInterface()
        network_interface.network = "global/networks/default"
        # No public IP by default — guest agent pulls to control plane (no inbound VNC).
        network_interface.access_configs = []

        items = [
            compute_v1.Items(key=k, value=v) for k, v in metadata.items()
        ]
        items.append(compute_v1.Items(key="startup-script", value=startup_script))
        meta = compute_v1.Metadata(items=items)

        instance = compute_v1.Instance()
        instance.name = name
        instance.machine_type = self._machine_type_url(zone, machine_type)
        instance.disks = [disk]
        instance.network_interfaces = [network_interface]
        instance.labels = labels
        instance.metadata = meta
        # Shielded VM defaults where available
        instance.scheduling = compute_v1.Scheduling(preemptible=False, automatic_restart=False)

        op = self._instances.insert(project=project, zone=zone, instance_resource=instance)
        self._wait(op)
        return self.get_instance(project=project, zone=zone, name=name)

    def get_instance(self, *, project: str, zone: str, name: str) -> GcpInstance:
        try:
            raw = self._instances.get(project=project, zone=zone, instance=name)
        except Exception as exc:
            raise GcpClientError(f"Failed to get instance {name}: {exc}") from exc
        labels = dict(raw.labels or {})
        meta_items = {}
        if raw.metadata and raw.metadata.items:
            for item in raw.metadata.items:
                if item.key != "startup-script":
                    meta_items[item.key] = item.value or ""
        external_ip = None
        for nic in raw.network_interfaces or []:
            for ac in nic.access_configs or []:
                if ac.nat_i_p:
                    external_ip = ac.nat_i_p
        return GcpInstance(
            name=raw.name,
            zone=zone,
            machine_type=(raw.machine_type or "").split("/")[-1],
            status=raw.status or "UNKNOWN",
            labels=labels,
            metadata=meta_items,
            self_link=raw.self_link or "",
            external_ip=external_ip,
        )

    def start_instance(self, *, project: str, zone: str, name: str) -> GcpInstance:
        op = self._instances.start(project=project, zone=zone, instance=name)
        self._wait(op)
        # Brief settle
        time.sleep(1)
        return self.get_instance(project=project, zone=zone, name=name)

    def stop_instance(self, *, project: str, zone: str, name: str) -> GcpInstance:
        op = self._instances.stop(project=project, zone=zone, instance=name)
        self._wait(op)
        return self.get_instance(project=project, zone=zone, name=name)

    def delete_instance(self, *, project: str, zone: str, name: str) -> None:
        op = self._instances.delete(project=project, zone=zone, instance=name)
        self._wait(op)


def build_gcp_client(*, fake: GcpComputeClient | None = None) -> GcpComputeClient:
    if fake is not None:
        return fake
    return RealGcpComputeClient()
