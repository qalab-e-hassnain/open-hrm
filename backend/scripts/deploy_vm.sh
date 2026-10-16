#!/usr/bin/env bash
# Ship the committed HEAD (never the working tree) to the QuickComm Azure VM.
#
#   VM=azureuser@<vm-ip> ./scripts/deploy_vm.sh            # deploy
#   VM=azureuser@<vm-ip> ./scripts/deploy_vm.sh --env      # also upload ./.env
#
# Env vars: VM (required), SSH_KEY (default ~/.ssh/deploy_key),
#           DIR (default /opt/hrm).
set -euo pipefail

: "${VM:?set VM=user@host}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/deploy_key}"
DIR="${DIR:-/opt/hrm}"
ssh_vm() { ssh -i "$SSH_KEY" "$VM" "$@"; }

cd "$(git rev-parse --show-toplevel)"
REV="$(git rev-parse --short HEAD)"
echo "Deploying $REV to $VM:$DIR"

ssh_vm "sudo mkdir -p $DIR && sudo chown \$(id -u):\$(id -g) $DIR"
# `backups` is excluded alongside `.env` because it holds root-owned database
# dumps this user cannot delete. Without that, rm exits non-zero, the && stops
# the untar — and rm has already deleted everything else first, leaving the
# directory empty with no source to serve from. That happened on 7 October.
git archive --format=tar HEAD | ssh_vm "find $DIR -mindepth 1 -maxdepth 1 ! -name .env ! -name backups -exec rm -rf {} + && tar -x -C $DIR && echo $REV > $DIR/REVISION"

if [[ "${1:-}" == "--env" ]]; then
  scp -i "$SSH_KEY" .env "$VM:$DIR/.env"
fi

ssh_vm bash -s <<REMOTE
set -euo pipefail
cd $DIR
test -f .env || { echo "No $DIR/.env — rerun with --env"; exit 1; }
docker compose build api
# Migrate with the new image while the old container still serves.
docker compose run --rm --no-deps -T api alembic upgrade head </dev/null
docker compose up -d api
for i in \$(seq 1 20); do
  curl -sf http://127.0.0.1:8002/health >/dev/null && { echo "HRM API healthy ($REV)"; exit 0; }
  sleep 3
done
docker compose logs --tail=80 api
exit 1
REMOTE
