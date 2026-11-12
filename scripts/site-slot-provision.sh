#!/usr/bin/env bash
# site-slot-provision.sh — light site-slot sync (scaffold + .env + optional db-provision).
# No ansible, no terraform. Platform base stack must already exist on the host.
# Usage (after CI bootstrap wrote app/.env + ansible/inventory.ini):
#   scripts/site-slot-provision.sh --site example.ru
set -euo pipefail
# shellcheck source=scripts/_lib.sh
source "$(dirname "$0")/_lib.sh"

die() { echo "site-slot: error: $*" >&2; exit 1; }

SITE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site) SITE="${2:-}"; shift 2 ;;
    *) die "unknown argument: $1 (use --site <fqdn>)" ;;
  esac
done

[[ -n "${SITE}" ]] || die "--site <fqdn> required"

ROOT="$(scripts_repo_root)"
cd "${ROOT}"
scripts_export_pythonpath "${ROOT}"

SITE="$(
  SITE_SLOT_DOMAIN="${SITE}" python3 - <<'PY'
import os
import sys

from scripts.sites import SiteError, normalize_domain

try:
    print(normalize_domain(os.environ["SITE_SLOT_DOMAIN"]), end="")
except SiteError as exc:
    print(f"site-slot: error: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc
PY
)" || exit 1

SITE_DIR="${ROOT}/app/sites/${SITE}"
COMPOSE_FILE="${SITE_DIR}/docker-compose.yml"
[[ -f "${COMPOSE_FILE}" ]] || die "missing ${COMPOSE_FILE} (run make site-add first)"

scripts_require_app_env "${ROOT}"
[[ -f "${ROOT}/app/.env" ]] || die "missing app/.env (run scripts/ci-deploy-bootstrap.sh first)"
[[ -f "${ROOT}/ansible/inventory.ini" ]] || die "missing ansible/inventory.ini (run scripts/ci-deploy-bootstrap.sh first)"

read -r DEPLOY_HOST DEPLOY_PORT DEPLOY_USER < <(scripts_inventory_connection "${ROOT}")
[[ -n "${DEPLOY_HOST}" && -n "${DEPLOY_PORT}" && -n "${DEPLOY_USER}" ]] \
  || die "cannot parse SSH connection from ansible/inventory.ini"

SSH="${DEPLOY_USER}@${DEPLOY_HOST}"
SSH_OPTS=(-p "${DEPLOY_PORT}" -o BatchMode=yes)
RSYNC_RSH="ssh -p ${DEPLOY_PORT} -o BatchMode=yes"
APP_ROOT="/opt/${PROJECT_HOSTNAME}/app"
REMOTE_SITE="${APP_ROOT}/sites/${SITE}"
REMOTE_SRC="/opt/${PROJECT_HOSTNAME}/var/site-src/${SITE}"
REMOTE_ENV="${APP_ROOT}/.env"

echo "site-slot: ${SITE} → ${SSH}:${REMOTE_SITE}"

# Client-side expansion is intentional: SITE/paths validated above.
# shellcheck disable=SC2029
ssh "${SSH_OPTS[@]}" "${SSH}" "mkdir -p '${REMOTE_SITE}' '${REMOTE_SRC}'"

rsync -e "${RSYNC_RSH}" -a --delete \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.build-hash' \
  "${SITE_DIR}/" "${SSH}:${REMOTE_SITE}/"

rsync -e "${RSYNC_RSH}" -a "${ROOT}/app/.env" "${SSH}:${REMOTE_ENV}"
# shellcheck disable=SC2029
ssh "${SSH_OPTS[@]}" "${SSH}" "chmod 600 '${REMOTE_ENV}'"

# Same as ansible: Prometheus needs host docker GID (APP_ENV often lacks it).
# REMOTE_ENV expanded on client; getent runs on server.
# shellcheck disable=SC2029,SC2087
ssh "${SSH_OPTS[@]}" "${SSH}" "bash -s" <<EOF
set -euo pipefail
env_file='${REMOTE_ENV}'
gid="\$(getent group docker | cut -d: -f3)"
[[ -n "\${gid}" ]] || { echo "site-slot: error: docker group missing on host" >&2; exit 1; }
if grep -q '^DOCKER_GID=' "\${env_file}"; then
  sed -i "s/^DOCKER_GID=.*/DOCKER_GID=\${gid}/" "\${env_file}"
else
  printf 'DOCKER_GID=%s\n' "\${gid}" >> "\${env_file}"
fi
EOF

if grep -qE '^[ \t]*DB_PASSWORD[[:space:]]*:' "${COMPOSE_FILE}"; then
  echo "site-slot: provisioning MySQL DB/user for ${SITE}"
  # shellcheck disable=SC2029
  ssh "${SSH_OPTS[@]}" "${SSH}" \
    "/opt/${PROJECT_HOSTNAME}/scripts/db-provision.sh --site '${SITE}'"
else
  echo "site-slot: skip db-provision (no DB_PASSWORD on site compose)"
fi

# shellcheck disable=SC2029
ssh "${SSH_OPTS[@]}" "${SSH}" "test -f '${REMOTE_SITE}/docker-compose.yml'" \
  || die "smoke failed: remote compose missing"

echo "site-slot: ${SITE} ok (scaffold + .env; code still via GitLab ▶)"
