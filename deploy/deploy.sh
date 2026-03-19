#!/usr/bin/env bash
set -euo pipefail

# Deploy perp-bot to a GCP Compute Engine instance.
# Usage: ./deploy.sh <instance-name> [zone]
#
# Prerequisites:
#   - gcloud CLI authenticated
#   - SSH access to the instance
#   - uv installed on the remote machine

INSTANCE="${1:?Usage: deploy.sh <instance-name> [zone]}"
ZONE="${2:-us-central1-a}"
REMOTE_DIR="/opt/perp-bot"

echo "==> Syncing code to ${INSTANCE}:${REMOTE_DIR}"
gcloud compute scp --recurse --zone="${ZONE}" \
    --exclude='.git,__pycache__,.venv,*.db,.env,node_modules' \
    . "${INSTANCE}:${REMOTE_DIR}"

echo "==> Installing dependencies"
gcloud compute ssh --zone="${ZONE}" "${INSTANCE}" -- bash -c "
    cd ${REMOTE_DIR}
    uv sync --frozen
"

echo "==> Installing systemd service"
gcloud compute ssh --zone="${ZONE}" "${INSTANCE}" -- sudo bash -c "
    cp ${REMOTE_DIR}/deploy/perp-bot.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable perp-bot
    systemctl restart perp-bot
"

echo "==> Checking service status"
gcloud compute ssh --zone="${ZONE}" "${INSTANCE}" -- \
    sudo systemctl status perp-bot --no-pager

echo "==> Deploy complete"
