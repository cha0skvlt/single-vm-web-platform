#!/usr/bin/env bash
# CI — write app/.env and ansible/inventory.ini from CI secrets (no stdout secrets).
set -euo pipefail
# shellcheck source=scripts/_lib.sh
source "$(dirname "$0")/_lib.sh"
ROOT="$(scripts_repo_root)"
cd "${ROOT}"

if [[ -z "${APP_ENV:-}" ]]; then
  echo "error: APP_ENV is not set" >&2
  exit 1
fi
if [[ -z "${DEPLOY_KNOWN_HOSTS:-}" ]]; then
  echo "error: DEPLOY_KNOWN_HOSTS is not set" >&2
  exit 1
fi

umask 077
scripts_export_pythonpath "${ROOT}"
export DEPLOY_KNOWN_HOSTS

# Resolve prod IPv4 from DEPLOY_KNOWN_HOSTS before sourcing app/.env (APP_ENV may set DEPLOY_HOST=#).
deploy_host="$(python3 <<'PY'
import os
import sys
from scripts.inventory import parse_deploy_host
try:
    print(
        parse_deploy_host(
            candidate=os.environ.get("DEPLOY_HOST"),
            known_hosts=os.environ["DEPLOY_KNOWN_HOSTS"],
        ),
        end="",
    )
except ValueError as exc:
    print(f"error: {exc}", file=sys.stderr)
    print(
        "hint: DEPLOY_KNOWN_HOSTS must include an ssh-keyscan line, e.g. "
        "[<ip>]:22 ssh-ed25519 AAAA... (no leading # comment lines only)",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc
PY
)" || exit 1

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"
known_hosts_filtered="$(python3 <<'PY'
import os
from scripts.inventory import filter_known_hosts_for_ssh
print(filter_known_hosts_for_ssh(os.environ["DEPLOY_KNOWN_HOSTS"]), end="")
PY
)"
if [[ -z "${known_hosts_filtered}" ]]; then
  echo "error: DEPLOY_KNOWN_HOSTS has no valid host key lines" >&2
  exit 1
fi
: > "${HOME}/.ssh/known_hosts"
printf '%s' "${known_hosts_filtered}" >> "${HOME}/.ssh/known_hosts"
chmod 600 "${HOME}/.ssh/known_hosts"

# Strip CRLF; normalize for deploy (no comments; PROD_PUBLIC_HOST=status.example.com).
APP_ENV_RAW="$(printf '%s' "${APP_ENV}" | sed 's/\r$//')"
export APP_ENV_RAW
python3 <<'PY'
import os
from pathlib import Path

from scripts.envfile import render_deploy_env

Path("app/.env").write_text(render_deploy_env(os.environ["APP_ENV_RAW"]), encoding="utf-8")
PY
chmod 600 app/.env

export DEPLOY_HOST_PARSED="${deploy_host}"
inv="$(python3 <<'PY'
import os
import sys
from pathlib import Path

from scripts.envfile import read_env_value
from scripts.inventory import ensure_inventory_hostname, render_inventory_ini

env_text = Path("app/.env").read_text(encoding="utf-8")
hostname = (read_env_value(env_text, "PROJECT_HOSTNAME") or "").strip()
if not hostname:
    print("error: PROJECT_HOSTNAME missing in APP_ENV", file=sys.stderr)
    raise SystemExit(1)
inv = render_inventory_ini(
    hostname=hostname,
    ansible_host=os.environ["DEPLOY_HOST_PARSED"],
)
ensure_inventory_hostname(inv, expected=hostname)
print(inv, end="")
PY
)"
printf '%s' "${inv}" > ansible/inventory.ini
PROJECT_HOSTNAME="$(python3 -c 'from pathlib import Path; from scripts.envfile import read_env_value; print((read_env_value(Path("app/.env").read_text(encoding="utf-8"), "PROJECT_HOSTNAME") or "").strip())')"
chmod 600 ansible/inventory.ini

if [[ -n "${GITHUB_ENV:-}" && -f "${GITHUB_ENV}" ]]; then
  if [[ "${PROJECT_HOSTNAME}" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] \
    && [[ "${deploy_host}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    {
      echo "PROJECT_HOSTNAME=${PROJECT_HOSTNAME}"
      echo "DEPLOY_HOST=${deploy_host}"
    } >> "${GITHUB_ENV}"
  fi
fi

echo "bootstrap: wrote app/.env and ansible/inventory.ini for ${PROJECT_HOSTNAME}@${deploy_host}"
