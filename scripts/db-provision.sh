#!/usr/bin/env bash
# db-provision.sh — idempotent shared MySQL DB/user provisioning (see ARCHITECTURE.md).
# Usage:
#   scripts/db-provision.sh --site example.ru
#   scripts/db-provision.sh --all          # every app/sites/<fqdn> with DB_PASSWORD (CI deploy)
#   scripts/db-provision.sh --provision-exporter
set -euo pipefail

die() { echo "db-provision: error: $*" >&2; exit 1; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${ROOT}/app"
ENV_FILE="${APP}/.env"

export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

MODE=""
SITE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site)
      [[ $# -ge 2 ]] || die "--site requires a domain"
      MODE=site
      SITE="$2"
      shift 2
      ;;
    --all)
      MODE=all
      shift
      ;;
    --provision-exporter)
      MODE=exporter
      shift
      ;;
    *)
      die "unknown argument: $1 (use --site <domain>, --all, or --provision-exporter)"
      ;;
  esac
done

[[ -n "${MODE}" ]] || die "specify --site <domain>, --all, or --provision-exporter"
[[ -f "${ENV_FILE}" ]] || die "missing ${ENV_FILE}"

read_env_password() {
  local key="$1"
  DB_PROVISION_ENV_FILE="${ENV_FILE}" DB_PROVISION_ENV_KEY="${key}" python3 - <<'PY'
import os
import sys
from pathlib import Path

from scripts.envfile import read_env_value

text = Path(os.environ["DB_PROVISION_ENV_FILE"]).read_text(encoding="utf-8")
key = os.environ["DB_PROVISION_ENV_KEY"]
value = read_env_value(text, key)
if not value:
    print(f"db-provision: error: missing {key} in app/.env", file=sys.stderr)
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

provision_site() {
  local site="$1"
  local ident env_key site_pw root_pw sql

  read -r ident env_key < <(
    DB_PROVISION_SITE="${site}" python3 - <<'PY'
import os
import sys

from scripts.sites import SiteError, db_env_key, db_ident_from_domain

try:
    ident = db_ident_from_domain(os.environ["DB_PROVISION_SITE"])
except SiteError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
print(ident, db_env_key(ident))
PY
  ) || die "invalid domain: ${site}"

  site_pw="$(read_env_password "${env_key}")" || exit 1
  root_pw="$(read_env_password "MYSQL_ROOT_PASSWORD")" || exit 1

  sql="$(
    DB_PROVISION_IDENT="${ident}" DB_PROVISION_SITE_PW="${site_pw}" python3 - <<'PY'
import os
from scripts.sites import build_site_provision_sql

print(build_site_provision_sql(os.environ["DB_PROVISION_IDENT"], os.environ["DB_PROVISION_SITE_PW"]), end="")
PY
  )"

  printf '%s' "${sql}" | mysql_root_exec "${root_pw}"
  echo "db-provision: site ${site} (${ident}) ok"
}

case "${MODE}" in
  site)
    provision_site "${SITE}"
    ;;
  all)
    mapfile -t sites < <(
      DB_PROVISION_ROOT="${ROOT}" python3 - <<'PY'
import os
from pathlib import Path
from scripts.sites import sites_needing_db

for domain in sites_needing_db(Path(os.environ["DB_PROVISION_ROOT"])):
    print(domain)
PY
    )
    if [[ ${#sites[@]} -eq 0 ]]; then
      echo "db-provision: --all: no sites with DB_PASSWORD"
      exit 0
    fi
    for site in "${sites[@]}"; do
      [[ -n "${site}" ]] || continue
      provision_site "${site}"
    done
    echo "db-provision: --all done (${#sites[@]} site(s))"
    ;;
  exporter)
    exporter_pw="$(read_env_password "MYSQL_EXPORTER_PASSWORD")" || exit 1
    root_pw="$(read_env_password "MYSQL_ROOT_PASSWORD")" || exit 1

    sql="$(
      DB_PROVISION_EXPORTER_PW="${exporter_pw}" python3 - <<'PY'
import os
from scripts.sites import build_exporter_provision_sql

print(build_exporter_provision_sql(os.environ["DB_PROVISION_EXPORTER_PW"]), end="")
PY
    )"

    printf '%s' "${sql}" | mysql_root_exec "${root_pw}"
    echo "db-provision: exporter user ok"
    ;;
esac
