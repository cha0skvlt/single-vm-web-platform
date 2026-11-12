#!/usr/bin/env bash
# deploy-site.sh — Site deploy (see ARCHITECTURE.md § Site CD)
# Called from GitLab CI job via SSH after rsync.
# Site app code is bind-mounted from var/site-src/<fqdn>.
# PHP / Node images may be built from app/sites/<fqdn>/{php,node}/Dockerfile (platform side).
# Usage: deploy-site.sh --site <fqdn>
set -euo pipefail

die() { echo "deploy-site: error: $*" >&2; exit 1; }

SITE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site) SITE="${2:-}"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "${SITE}" ]] || die "--site <fqdn> required"
[[ "${SITE}" =~ ^[a-z0-9][a-z0-9.-]*$ ]] || die "invalid site name: ${SITE}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${ROOT}/app"
ENV_FILE="${APP}/.env"
SITE_DIR="${APP}/sites/${SITE}"
SRC_DIR="${ROOT}/var/site-src/${SITE}"
COMPOSE_FILE="${SITE_DIR}/docker-compose.yml"
PROJECT="site-${SITE//./-}"

export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

compose() {
  docker compose \
    -p "${PROJECT}" \
    --env-file "${ENV_FILE}" \
    -f "${COMPOSE_FILE}" \
    "$@"
}

read_env_password() {
  local key="$1"
  DEPLOY_SITE_ENV_FILE="${ENV_FILE}" DEPLOY_SITE_ENV_KEY="${key}" python3 - <<'PY'
import os
import sys
from pathlib import Path

from scripts.envfile import read_env_value

text = Path(os.environ["DEPLOY_SITE_ENV_FILE"]).read_text(encoding="utf-8")
key = os.environ["DEPLOY_SITE_ENV_KEY"]
value = read_env_value(text, key)
if not value:
    print(f"deploy-site: error: missing {key} in app/.env", file=sys.stderr)
    raise SystemExit(1)
print(value)
PY
}

mysql_root_exec() {
  local root_pw="$1"
  shift
  (
    cd "${APP}" || exit 1
    docker compose --env-file "${ENV_FILE}" exec -T mysql \
      mysql -uroot "-p${root_pw}" "$@"
  )
}

# Import *.sql from site-src root into site DB only when the database has zero tables.
import_sql_if_empty() {
  local ident env_key root_pw table_count sql_file
  local -a sql_files=()

  if ! grep -qE '^[ \t]*DB_PASSWORD[[:space:]]*:' "${COMPOSE_FILE}"; then
    echo "deploy-site: sql import skip (no MySQL on site compose)"
    return 0
  fi

  shopt -s nullglob
  sql_files=("${SRC_DIR}"/*.sql)
  shopt -u nullglob
  if [[ ${#sql_files[@]} -eq 0 ]]; then
    echo "deploy-site: sql import skip (no *.sql in ${SRC_DIR})"
    return 0
  fi

  [[ -f "${ENV_FILE}" ]] || die "missing ${ENV_FILE}"

  read -r ident env_key < <(
    DEPLOY_SITE_SITE="${SITE}" python3 - <<'PY'
import os
import sys

from scripts.sites import SiteError, db_env_key, db_ident_from_domain

try:
    ident = db_ident_from_domain(os.environ["DEPLOY_SITE_SITE"])
except SiteError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
print(ident, db_env_key(ident))
PY
  ) || die "invalid domain: ${SITE}"

  # Ensure site DB password exists (provisioned); import runs as root into that schema.
  read_env_password "${env_key}" >/dev/null || exit 1
  root_pw="$(read_env_password "MYSQL_ROOT_PASSWORD")" || exit 1

  table_count="$(
    mysql_root_exec "${root_pw}" -N -e \
      "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${ident}' AND table_type='BASE TABLE';"
  )"
  table_count="$(echo "${table_count}" | tr -d '[:space:]')"
  [[ "${table_count}" =~ ^[0-9]+$ ]] || die "cannot count tables in ${ident}"

  if [[ "${table_count}" -gt 0 ]]; then
    echo "deploy-site: sql import skip (${ident} already has ${table_count} table(s))"
    return 0
  fi

  # shellcheck disable=SC2207
  IFS=$'\n' sql_files=($(printf '%s\n' "${sql_files[@]}" | LC_ALL=C sort))
  for sql_file in "${sql_files[@]}"; do
    echo "deploy-site: sql import ${sql_file} → ${ident}"
    mysql_root_exec "${root_pw}" "${ident}" < "${sql_file}" \
      || die "sql import failed: ${sql_file}"
  done
  echo "deploy-site: sql import done (${#sql_files[@]} file(s) → ${ident})"
}

[[ -f "${COMPOSE_FILE}" ]] || die "site not found: ${SITE_DIR} (run: make site-add)"
[[ -d "${SRC_DIR}" ]] || die "no sources: ${SRC_DIR} (runner rsync must send files first)"

# Composer deps are not in git; GitLab rsync must --exclude=vendor.
# Run as deploy (not container root) so vendor stays writable for the next ▶.
if [[ -f "${SRC_DIR}/composer.json" ]]; then
  echo "deploy-site: composer install (${SITE})"
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e COMPOSER_HOME=/tmp/composer \
    -v "${SRC_DIR}:/app" \
    -w /app \
    composer:2 \
    install --no-dev --optimize-autoloader --no-interaction --ignore-platform-reqs
fi

# PHP / Node images live on the platform (not in the site GitLab repo).
if [[ -f "${SITE_DIR}/php/Dockerfile" ]]; then
  echo "deploy-site: build PHP image (${SITE})"
  compose build
elif [[ -f "${SITE_DIR}/node/Dockerfile" ]]; then
  echo "deploy-site: build Node image (${SITE})"
  compose build
fi

echo "deploy-site: starting ${SITE}"
compose up -d --remove-orphans

import_sql_if_empty

echo "deploy-site: done → ${SITE}"
