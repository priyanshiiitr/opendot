#!/bin/bash
# Bootstrap AI Teammates control plane on GCE without requiring GCS.
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip git curl ca-certificates

APP_ROOT=/opt/ai-teammates
mkdir -p "$APP_ROOT/data/computers" /opt/ai-teammates-secrets
cd "$APP_ROOT"

META="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
HDR=(-H "Metadata-Flavor: Google")

REPO_URL=$(curl -sf "${HDR[@]}" "${META}/aitm-repo-url" || true)
REPO_BRANCH=$(curl -sf "${HDR[@]}" "${META}/aitm-repo-branch" || echo "m10-cloud-computers")
PUBLIC_URL=$(curl -sf "${HDR[@]}" "${META}/aitm-public-url" || true)
SA_B64=$(curl -sf "${HDR[@]}" "${META}/aitm-sa-b64" || true)

if [[ -z "${REPO_URL}" ]]; then
  REPO_URL="https://github.com/priyanshiiitr/opendot.git"
fi

# Clone or refresh app source
if [[ -d "$APP_ROOT/repo/.git" ]]; then
  cd "$APP_ROOT/repo"
  git fetch --depth 1 origin "$REPO_BRANCH" || true
  git checkout -f "FETCH_HEAD" || git checkout -f "$REPO_BRANCH" || true
else
  rm -rf "$APP_ROOT/repo"
  git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$APP_ROOT/repo" \
    || git clone --depth 1 "$REPO_URL" "$APP_ROOT/repo"
fi

rm -rf "$APP_ROOT/backend"
cp -a "$APP_ROOT/repo/backend" "$APP_ROOT/backend"

if [[ -n "${SA_B64}" ]]; then
  echo "${SA_B64}" | base64 -d > /opt/ai-teammates-secrets/sa-compute.json
  chmod 600 /opt/ai-teammates-secrets/sa-compute.json
fi

EXT_IP=$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || true)
if [[ -n "${PUBLIC_URL}" ]]; then
  CTRL="${PUBLIC_URL}"
elif [[ -n "${EXT_IP}" ]]; then
  CTRL="http://${EXT_IP}:8000"
else
  CTRL="http://127.0.0.1:8000"
fi

PROJECT=$(curl -sf -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/project/project-id)

cat > "$APP_ROOT/backend/.env" <<EOF
AI_TEAMMATES_MODEL_PROVIDER=mock
AI_TEAMMATES_DATABASE_URL=sqlite:////opt/ai-teammates/data/ai_teammates.db
AI_TEAMMATES_AUTOMATION_WORKER_ENABLED=true
AI_TEAMMATES_BIND_HOST=0.0.0.0
AI_TEAMMATES_BIND_PORT=8000
AI_TEAMMATES_REMOTE_ACCESS_ENABLED=true
AI_TEAMMATES_LOCAL_TRUST_LOOPBACK=true
AI_TEAMMATES_COMPUTER_DEFAULT_PROVIDER=local_browser
AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE=false
AI_TEAMMATES_COMPUTER_CREDIT_FUNDED_ENABLED=true
AI_TEAMMATES_GCP_PROJECT_ID=${PROJECT}
AI_TEAMMATES_GCP_ZONE=us-central1-a
AI_TEAMMATES_GCP_MACHINE_TYPE=e2-micro
AI_TEAMMATES_GCP_ALLOWED_MACHINE_TYPES=e2-micro,e2-small
AI_TEAMMATES_GCP_BOOT_DISK_GB=20
AI_TEAMMATES_GCP_MAX_CONCURRENT_VMS=1
AI_TEAMMATES_GCP_CONTROL_PLANE_URL=${CTRL}
AI_TEAMMATES_GCP_CREDENTIALS_FILE=/opt/ai-teammates-secrets/sa-compute.json
AI_TEAMMATES_BROWSER_ENABLED=true
AI_TEAMMATES_BROWSER_HEADLESS=true
AI_TEAMMATES_COMPUTERS_ROOT_DIR=/opt/ai-teammates/data/computers
EOF

cd "$APP_ROOT/backend"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install --with-deps chromium || true

cat >/etc/systemd/system/ai-teammates.service <<EOF
[Unit]
Description=AI Teammates control plane
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_ROOT}/backend
Environment=GOOGLE_APPLICATION_CREDENTIALS=/opt/ai-teammates-secrets/sa-compute.json
ExecStart=${APP_ROOT}/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ai-teammates
systemctl restart ai-teammates
echo "Control plane bootstrap complete URL=${CTRL}" >/var/log/ai-teammates-bootstrap.txt
