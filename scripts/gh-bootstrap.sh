#!/usr/bin/env bash
# Push deploy secrets to GitHub (repository level, Free-tier friendly).
# Template CI does not deploy to a cloud.
set -euo pipefail
# shellcheck source=scripts/_lib.sh
source "$(dirname "$0")/_lib.sh"
ROOT="$(scripts_repo_root)"
cd "${ROOT}"

# Optional cloud-plan secrets if you add a plan job; this template CI does not deploy.

die() {
  echo "error: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "$1 not found"
}

require_cmd gh
require_cmd ssh-keyscan

if ! gh auth status >/dev/null 2>&1; then
  die "gh is not authenticated (run: gh auth login)"
fi

OWNER_REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)" || true
if [[ -z "${OWNER_REPO}" ]]; then
  die "could not resolve repo from gh (run inside this repository git checkout)"
fi
OWNER="${OWNER_REPO%%/*}"
REPO="${OWNER_REPO##*/}"
SECRETS_URL="https://github.com/${OWNER}/${REPO}/settings/secrets/actions"

[[ -f app/.env ]] || die "app/.env missing (create from app/.env.example before bootstrap)"
[[ -f keys/ci_deploy ]] || die "keys/ci_deploy missing (run: make ci-setup)"

FIP="${FIP:-}"
SSH_PORT="${SSH_PORT:-}"
if [[ -z "${FIP}" || -z "${SSH_PORT}" ]] && [[ -f ansible/inventory.ini ]]; then
  read -r _fip _port _user < <(scripts_inventory_connection "${ROOT}") || true
  FIP="${FIP:-${_fip:-}}"
  SSH_PORT="${SSH_PORT:-${_port:-22}}"
fi
SSH_PORT="${SSH_PORT:-22}"
[[ -n "${FIP}" ]] || die "FIP is not set and ansible/inventory.ini has no ansible_host"

echo "gh-bootstrap: repo ${OWNER}/${REPO}, target host ${FIP}:${SSH_PORT}"
echo "tier: repository secrets (Actions → Secrets), works on GitHub Free + private repo"
echo "note: environment secrets / required reviewers need Pro for private — not used"
echo ""

# --- Repository secrets for deploy (stdin only; never echo contents) ---
scripts_export_pythonpath "${ROOT}"
python3 <<'PY' | gh secret set APP_ENV
from pathlib import Path

from scripts.envfile import render_deploy_env

print(render_deploy_env(Path("app/.env").read_text(encoding="utf-8")), end="")
PY
echo "secret: APP_ENV (repository, render_deploy_env from app/.env)"

gh secret set DEPLOY_SSH_KEY < keys/ci_deploy
echo "secret: DEPLOY_SSH_KEY (repository)"

known_hosts="$(ssh-keyscan -p "${SSH_PORT}" -t ed25519 "${FIP}" 2>/dev/null || true)"
[[ -n "${known_hosts}" ]] || die "ssh-keyscan returned no lines for ${FIP}:${SSH_PORT}"
# Raw ssh-keyscan lines only (no # comments — CI parser skips comments but needs one key line).
printf '%s' "${known_hosts}" | gh secret set DEPLOY_KNOWN_HOSTS
echo "secret: DEPLOY_KNOWN_HOSTS (repository)"

export FIP
python3 <<'PY'
import os
from pathlib import Path

from scripts.envfile import read_env_value
from scripts.inventory import render_inventory_ini, sync_inventory_group

env_path = Path("app/.env")
expected = (read_env_value(env_path.read_text(encoding="utf-8"), "PROJECT_HOSTNAME") or "").strip()
if not expected:
    raise SystemExit("error: PROJECT_HOSTNAME missing in app/.env")

inv_path = Path("ansible/inventory.ini")
fip = os.environ.get("FIP", "").strip()
if inv_path.is_file():
    text, changed = sync_inventory_group(inv_path.read_text(encoding="utf-8"), expected=expected)
    if changed:
        inv_path.write_text(text, encoding="utf-8")
        inv_path.chmod(0o600)
        print(f"inventory: fixed group -> [{expected}]")
elif fip:
    inv_path.write_text(
        render_inventory_ini(hostname=expected, ansible_host=fip),
        encoding="utf-8",
    )
    inv_path.chmod(0o600)
    print(f"inventory: wrote ansible/inventory.ini [{expected}]")
PY

echo ""
echo "checklist:"
echo "  [x] APP_ENV, DEPLOY_SSH_KEY, DEPLOY_KNOWN_HOSTS (repository secrets)"
echo "  [ ] make ci-key-install on the VM (if not done)"
echo "  Template CI does not deploy. Local: make deploy / make deploy-ci"
echo "  Secrets URL: ${SECRETS_URL}"
echo ""
echo "CI uses keys/ci_deploy (revocable); operator key in keys/deploy.pub is unchanged."
