#!/usr/bin/env bash
# Deploy wildcard / purchased TLS from ssl/<domain>/ (or certs-staging/<domain>/).
# usage: scripts/deploy-wildcard-cert.sh <domain>
set -euo pipefail
# shellcheck source=scripts/_lib.sh
source "$(dirname "$0")/_lib.sh"

DOMAIN="${1:?usage: $0 <domain>}"
ROOT="$(scripts_repo_root)"
cd "${ROOT}"

scripts_require_app_env "${ROOT}"
scripts_export_pythonpath "${ROOT}"

python3 -m scripts.cli check-staging "$DOMAIN"
python3 -m scripts.cli materialize-staging "$DOMAIN"

DOMAIN_YML="${ROOT}/app/traefik/dynamic/${DOMAIN}.yml"
DEFAULT_YML="${ROOT}/app/traefik/dynamic/certs-default.yml"
python3 -c "from pathlib import Path; from scripts.certs import validate_yaml_file; validate_yaml_file(Path('${DOMAIN_YML}'))"

if ! FIP="$(cd terraform && terraform output -raw public_ip_address 2>/dev/null)"; then
  echo "error: cannot read terraform output public_ip_address (run terraform init/apply in terraform/)" >&2
  exit 1
fi

read -r _DEPLOY_HOST DEPLOY_PORT DEPLOY_USER < <(scripts_inventory_connection "${ROOT}")
SSH="${DEPLOY_USER}@${FIP}"
CERT_SRC="${ROOT}/app/certs/${DOMAIN}"
REMOTE_CERT="/opt/${PROJECT_HOSTNAME}/app/certs/${DOMAIN}"
APP_ROOT="/opt/${PROJECT_HOSTNAME}/app"

rsync -e "ssh -p ${DEPLOY_PORT}" -a "${CERT_SRC}/" "${SSH}:${REMOTE_CERT}/"
ssh -p "${DEPLOY_PORT}" "${SSH}" "chmod 600 ${REMOTE_CERT}/privkey.pem"
rsync -e "ssh -p ${DEPLOY_PORT}" -a "$DOMAIN_YML" "${SSH}:${APP_ROOT}/traefik/dynamic/${DOMAIN}.yml"
rsync -e "ssh -p ${DEPLOY_PORT}" -a "$DEFAULT_YML" "${SSH}:${APP_ROOT}/traefik/dynamic/certs-default.yml"
ssh -p "${DEPLOY_PORT}" "${SSH}" "rm -f ${APP_ROOT}/traefik/dynamic/certs.domains.yml"
echo "deployed ${DOMAIN} — Traefik file-provider watch (${DOMAIN}.yml), no restart required"
