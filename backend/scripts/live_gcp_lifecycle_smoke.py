"""One-shot credit-funded GCP lifecycle smoke test. Deletes the VM at the end."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.browser.policy import NavigationPolicy
from app.computers.gcp_client import RealGcpComputeClient
from app.computers.manager import ComputerManager
from app.config import Settings, get_settings
from app.database import ComputerRecord, Teammate, init_db


async def main() -> None:
    get_settings.cache_clear()
    td = Path(tempfile.mkdtemp(prefix="gcp-live-"))
    db_path = td / "live.db"
    root = td / "computers"
    cred = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS",
        os.environ.get("AI_TEAMMATES_GCP_CREDENTIALS_FILE", ""),
    )
    settings = Settings(
        database_url=f"sqlite:///{db_path.as_posix()}",
        computers_root_dir=str(root),
        automation_worker_enabled=False,
        model_provider="mock",
        computer_allow_billable=False,
        computer_credit_funded_enabled=True,
        gcp_project_id=os.environ.get("AI_TEAMMATES_GCP_PROJECT_ID", "ai-teamates"),
        gcp_zone=os.environ.get("AI_TEAMMATES_GCP_ZONE", "us-central1-a"),
        gcp_machine_type=os.environ.get("AI_TEAMMATES_GCP_MACHINE_TYPE", "e2-micro"),
        gcp_max_concurrent_vms=1,
        gcp_control_plane_url=os.environ.get(
            "AI_TEAMMATES_GCP_CONTROL_PLANE_URL", "http://127.0.0.1:8000"
        ),
        gcp_credentials_file=cred,
    )
    from app import database as database_mod

    database_mod.engine = create_engine(
        f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False}
    )
    database_mod.SessionLocal = sessionmaker(bind=database_mod.engine, expire_on_commit=False)
    init_db()
    client = RealGcpComputeClient()
    print("credentials_ready", client.credentials_ready())
    mgr = ComputerManager(
        settings, database_mod.SessionLocal, NavigationPolicy(fixture_mode=False), gcp_client=client
    )
    with database_mod.SessionLocal() as db:
        t = db.query(Teammate).first()
        if not t:
            t = Teammate(name="GCP Live", description="", system_prompt="x")
            db.add(t)
            db.commit()
            db.refresh(t)
        row = db.query(ComputerRecord).filter_by(teammate_id=t.id).first()
        if row:
            db.delete(row)
            db.commit()
        tid = t.id

    est = await mgr.provision(tid, provider="gcp_compute", confirm_estimate=False)
    cost = est.get("estimated_cost") or {}
    print(
        "ESTIMATE",
        est.get("requires_confirmation"),
        cost.get("usd_per_hour_approx"),
        cost.get("machine_type"),
    )
    print("Creating VM (confirm_estimate=true)...")
    created = await mgr.provision(tid, provider="gcp_compute", confirm_estimate=True)
    prov = created.get("provision") or {}
    print("CREATED", prov.get("created"), prov.get("instance_name"), prov.get("status"))
    started = await mgr.start(tid)
    print("STARTED", started.get("state"), started.get("provider"))
    health = await mgr.health(tid)
    h = health.get("health") or {}
    print("HEALTH", h.get("healthy"), h.get("status"), h.get("instance_name"))
    stopped = await mgr.stop(tid)
    print("STOPPED", stopped.get("state"))
    health2 = await mgr.health(tid)
    h2 = health2.get("health") or {}
    print("HEALTH_AFTER_STOP", h2.get("status"), h2.get("healthy"))
    deleted = await mgr.delete_computer(tid, confirm=True)
    print("DELETED", deleted.get("deleted"), deleted.get("computer_id"))
    print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
