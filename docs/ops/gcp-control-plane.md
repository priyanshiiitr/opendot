# Ops: remote control plane + GCP execution VMs

## Goal

Keep the **agent control plane** (FastAPI, AutomationWorker, database) running when the laptop is off. Provision **separate** credit-funded Compute Engine VMs for teammate execution.

```text
[Phone / laptop clients]
        |  HTTPS + pairing (M9)
        v
[Control plane VM or container]  ← always-on
  - uvicorn + worker
  - SQLite/Postgres volume
  - ComputerManager / gcp_compute
        |  Compute Engine API (SA credentials on control plane only)
        v
[Execution VM e2-micro]  ← on demand, max 1
  - guest agent (pull model)
  - no public VNC
```

## Do not

- Put service-account JSON in git or chat.
- Open firewall for raw VNC/RDP to `0.0.0.0/0`.
- Set `COMPUTER_ALLOW_BILLABLE=true`.
- Upgrade free trial to paid / enable PAYG without a new explicit approval.
- Auto-migrate production data from the laptop SQLite without a backup plan.

## Minimal control-plane VM (credit-funded)

1. Create a small GCE instance (e.g. `e2-micro` or `e2-small`) in the same project.
2. Attach a persistent disk for `./data`.
3. Install Python 3.11+, clone the app, `pip install -r backend/requirements.txt`.
4. Configure `.env` on the server (credit-funded flags + GCP project + credentials file path).
5. Run: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` behind HTTPS (Caddy/nginx) on a private VPN or IAP-protected URL.
6. Set `AI_TEAMMATES_GCP_CONTROL_PLANE_URL` to that HTTPS origin.
7. Enable `AI_TEAMMATES_REMOTE_ACCESS_ENABLED` only on a private network path (Tailscale recommended).

## Execution VM lifecycle (app-managed)

Use the Computer UI / API — do not manually create unlabeled VMs:

1. Estimate (`confirm_estimate=false`) → review USD/hr approximation.
2. Confirm create (`confirm_estimate=true`) → at most one active `gcp_compute`.
3. Start / health / takeover / stop.
4. Delete with `confirm=true` to remove the instance and local workspace mirror.

## Verification checklist

- [ ] `gcloud` auth works on the control plane host
- [ ] Compute Engine API enabled
- [ ] Credit-funded env flags set; billable flag false
- [ ] One VM created via the app
- [ ] Guest agent heartbeat appears in health
- [ ] Preview uploads (placeholder PNG until full desktop image)
- [ ] Stop leaves control-plane task DB intact
- [ ] Delete removes the GCE instance
- [ ] Second concurrent provision is refused

## Oracle Cloud fallback

If GCP trial access is unavailable, stop and report. Do not auto-switch. Oracle Always Free has capacity and idle-reclamation limits that need a separate ADR.