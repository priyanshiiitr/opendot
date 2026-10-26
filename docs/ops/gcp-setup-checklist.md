# GCP setup checklist (credit-funded demo)

Follow these steps in order. **Do not paste service-account JSON or private keys into chat or git.**

Spending rules for this project:

- Out-of-pocket spend: **not authorized**
- Free trial / promotional credits: OK when you enable credit-funded mode
- Do **not** upgrade the free trial to a paid / pay-as-you-go plan without a new explicit approval

---

## Part A — You do this in the browser (login + billing)

### A1. Start the free trial

1. Open: https://cloud.google.com/free  
2. Click **Get started for free** / **Try for free**.  
3. Sign in with your Google account.  
4. Complete identity / payment verification.  
   - Google may place a temporary authorization hold (not a real charge).  
   - You should receive about **$300 credit for 90 days** if eligible.  
5. Accept terms and finish signup.

### A2. Create or select a project

1. Open: https://console.cloud.google.com/  
2. Top bar → project picker → **New project**.  
3. Name it e.g. `ai-teammates-demo`.  
4. Copy the **Project ID** (not only the display name). You will put it in `.env` as `AI_TEAMMATES_GCP_PROJECT_ID`.

### A3. Enable Compute Engine

1. Open: https://console.cloud.google.com/apis/library/compute.googleapis.com  
2. Select your project.  
3. Click **Enable**. Wait until it finishes.

### A4. Create a service account (credentials identity)

1. Open: https://console.cloud.google.com/iam-admin/serviceaccounts  
2. **Create service account**.  
   - Name: `ai-teammates-compute`  
   - Continue.  
3. Grant role: **Compute Admin** (`roles/compute.admin`) for the first demo  
   (later you can narrow this).  
4. Finish creation.

### A5. Download a JSON key (the credentials file)

1. Still on Service accounts → click the new account email.  
2. **Keys** tab → **Add key** → **Create new key** → **JSON** → **Create**.  
3. Your browser downloads a `.json` file once. Store it here (recommended):

```text
%USERPROFILE%\.ai-teammates\gcp\sa-compute.json
```

Example full path:

```text
C:\Users\<You>\.ai-teammates\gcp\sa-compute.json
```

4. Never put that file inside the git repo (`E:\opendot`).  
5. Never paste its contents into Cursor chat.

Docs: https://docs.cloud.google.com/iam/docs/keys-create-delete

---

## Part B — On this PC (config)

### B1. Confirm Cloud SDK is installed

Open a **new** PowerShell window after install:

```powershell
gcloud --version
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud auth application-default login
```

`application-default login` is an alternative to a JSON key for local testing. For the app’s `AI_TEAMMATES_GCP_CREDENTIALS_FILE` path, the JSON key is clearer.

### B2. Edit `backend\.env` (path only — no JSON contents)

Add or set:

```env
AI_TEAMMATES_COMPUTER_DEFAULT_PROVIDER=local_browser
AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE=false
AI_TEAMMATES_COMPUTER_CREDIT_FUNDED_ENABLED=true
AI_TEAMMATES_GCP_PROJECT_ID=YOUR_PROJECT_ID
AI_TEAMMATES_GCP_ZONE=us-central1-a
AI_TEAMMATES_GCP_MACHINE_TYPE=e2-micro
AI_TEAMMATES_GCP_ALLOWED_MACHINE_TYPES=e2-micro,e2-small
AI_TEAMMATES_GCP_BOOT_DISK_GB=20
AI_TEAMMATES_GCP_MAX_CONCURRENT_VMS=1
AI_TEAMMATES_GCP_CONTROL_PLANE_URL=http://127.0.0.1:8000
AI_TEAMMATES_GCP_CREDENTIALS_FILE=C:\Users\<You>\.ai-teammates\gcp\sa-compute.json
```

Notes:

- Keep `ALLOW_BILLABLE=false`.  
- `CONTROL_PLANE_URL` must be reachable from the guest VM later. For a first laptop demo, `http://127.0.0.1:8000` only works for local Fake tests — a **real** execution VM cannot call your laptop’s localhost. For a real VM demo you need a publicly reachable or VPN URL for the control plane (see `gcp-control-plane.md`).  
- For the very first “does GCE create/stop/delete work?” check from this laptop, the API calls use your credentials; the guest agent heartbeat needs a reachable control plane.

### B3. Restart the backend

```powershell
cd E:\opendot\backend
..\..\.venv\Scripts\Activate.ps1   # or: E:\opendot\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

---

## Part C — First VM test in the app

1. Open the desktop app / UI → select a teammate → **Computer**.  
2. If that teammate already has a local computer: **Delete computer** (confirm).  
3. Click **Provision GCP (credit-funded)**.  
4. Read the cost estimate dialog → confirm.  
5. Then: **Start** → **Health check** → **Stop** → **Delete computer**.  

If provision fails, the error message will say what is missing (credits flag, project id, credentials file, concurrent limit, etc.).

---

## Part D — Laptop-independent control plane (later)

When you want work to continue with the laptop off, follow `docs/ops/gcp-control-plane.md`: deploy API + worker + DB to a small always-on host, then point `GCP_CONTROL_PLANE_URL` at that HTTPS URL.

---

## Quick link list

| Step | Link |
|------|------|
| Free trial | https://cloud.google.com/free |
| Console | https://console.cloud.google.com/ |
| Enable Compute Engine | https://console.cloud.google.com/apis/library/compute.googleapis.com |
| Service accounts | https://console.cloud.google.com/iam-admin/serviceaccounts |
| Create keys docs | https://docs.cloud.google.com/iam/docs/keys-create-delete |

---

## What I (the agent) can do for you

- Install Cloud SDK, prepare folders, wire `.env` **paths**, open the right pages.  
- After you place the JSON key file and project id, run a **dry estimate** / Fake tests / help debug errors.

## What only you can do

- Google login, free-trial signup, payment verification.  
- Download the JSON key (Google shows it once).  
- Decide not to upgrade to paid billing.
