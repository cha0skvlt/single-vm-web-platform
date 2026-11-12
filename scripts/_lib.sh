#!/usr/bin/env bash
# Shared helpers for scripts/*.sh — source: source "$(dirname "$0")/_lib.sh"

scripts_repo_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "${script_dir}/.." && pwd
}

scripts_export_pythonpath() {
  local root="${1:-$(scripts_repo_root)}"
  export PYTHONPATH="${root}${PYTHONPATH:+:${PYTHONPATH}}"
}

scripts_load_app_env() {
  local root="${1:-$(scripts_repo_root)}"
  local env_file="${root}/app/.env"
  if [[ -n "${PROJECT_HOSTNAME:-}" ]]; then
    return 0
  fi
  if [[ ! -f "${env_file}" ]]; then
    echo "error: create ${env_file} from app/.env.example" >&2
    return 1
  fi
  set -a
  # shellcheck source=/dev/null
  source "${env_file}"
  set +a
  if [[ -z "${PROJECT_HOSTNAME:-}" ]]; then
    echo "error: PROJECT_HOSTNAME must be set in app/.env" >&2
    return 1
  fi
}

scripts_require_app_env() {
  local root="${1:-$(scripts_repo_root)}"
  if [[ -z "${PROJECT_HOSTNAME:-}" ]]; then
    scripts_load_app_env "${root}"
  fi
  if [[ -z "${PROJECT_HOSTNAME:-}" ]]; then
    echo "error: PROJECT_HOSTNAME must be set in app/.env" >&2
    return 1
  fi
}

scripts_load_s3_backend_env() {
  local root="${1:-$(scripts_repo_root)}"
  local s3_env="${root}/terraform/s3-backend.env"
  if [[ -f "${s3_env}" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${s3_env}"
    set +a
  fi
}

scripts_inventory_connection() {
  local root="${1:-$(scripts_repo_root)}"
  scripts_export_pythonpath "${root}"
  (
    cd "${root}" || exit 1
    python3 - <<'PY'
from pathlib import Path
from scripts.inventory import parse_inventory_connection

host, port, user = parse_inventory_connection(
    Path("ansible/inventory.ini").read_text(encoding="utf-8")
)
print(host, port, user)
PY
  )
}
