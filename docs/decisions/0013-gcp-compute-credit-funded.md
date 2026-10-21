# ADR 0013 — GCP Compute Engine + credit-funded guardrails

## Status

Accepted (post-M10 extension) — **code path implemented; live VM not verified until credentials exist**

## Context

Owner chose Google Cloud Compute Engine for the first real-cloud demo, using free-trial / promotional credits only. Out-of-pocket spend is **not** authorized. `AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE` stays `false`. Local default remains `local_browser`.

## Decision

1. **Provider id:** `gcp_compute` (isolation_tier `vm`) behind the existing `ComputerProvider` protocol.
2. **Separate gate:** `AI_TEAMMATES_COMPUTER_CREDIT_FUNDED_ENABLED` — distinct from billable stub refusal.
3. **Hard limits:** max 1 concurrent execution VM; machine types allowlisted (`e2-micro`, `e2-small`); estimate + `confirm_estimate=true` required before create.
4. **No public VNC:** guest agent pulls from `AI_TEAMMATES_GCP_CONTROL_PLANE_URL` with a hashed agent token.
5. **`billable_cloud`:** continues to refuse creation.
6. **`mock_cloud`:** tests only.
7. **Oracle fallback:** only if GCP trial is unavailable — report; do not auto-switch.

## What is verified vs not

| Item | Status |
|------|--------|
| Fake client contract tests (provision/start/stop/delete/takeover/limits) | Verified in CI |
| Real GCE VM create + lifecycle | **Blocked** until gcloud/ADC + project + credits configured by owner |
| Browser preview from Chromium on VM | Placeholder guest agent PNG until desktop stack is installed on the image |
| Remote control-plane deploy | Documented procedure; not auto-deployed from this machine |

## Owner setup (required)

1. Install Google Cloud SDK (`gcloud`) and sign in on an eligible free-trial account.
2. Create/select a project; enable Compute Engine API.
3. Create a least-privilege service account (compute.instances.* on the demo project) and store the JSON **outside the repo**.
4. Deploy the control plane (API + worker + SQLite/Postgres volume) to a small always-on host reachable by the guest agent URL.
5. Set env (never paste secrets in chat):

```text
AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE=false
AI_TEAMMATES_COMPUTER_CREDIT_FUNDED_ENABLED=true
AI_TEAMMATES_GCP_PROJECT_ID=...
AI_TEAMMATES_GCP_ZONE=us-central1-a
AI_TEAMMATES_GCP_MACHINE_TYPE=e2-micro
AI_TEAMMATES_GCP_MAX_CONCURRENT_VMS=1
AI_TEAMMATES_GCP_CONTROL_PLANE_URL=https://<your-control-plane>
AI_TEAMMATES_GCP_CREDENTIALS_FILE=C:\path\to\sa.json
```

6. Delete any existing local computer for the teammate, then **Provision GCP (credit-funded)** in the UI (estimate → confirm).

## Consequences

- Until credentials exist, provision fails closed with a clear error.
- Billing alerts are not treated as spend caps.
- Stopping a VM keeps the disk until delete(confirm); delete removes the instance.