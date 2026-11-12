#!/usr/bin/env bash
# Load app/.env (PROJECT_HOSTNAME for Ansible/compose paths). Usage:
#   scripts/with-project-env.sh terraform -chdir=terraform plan
#   scripts/with-project-env.sh ansible-playbook -i ansible/inventory.ini ansible/playbook.yml
set -euo pipefail
# shellcheck source=scripts/_lib.sh
source "$(dirname "$0")/_lib.sh"
ROOT="$(scripts_repo_root)"
cd "${ROOT}"
export ANSIBLE_CONFIG="${ROOT}/ansible/ansible.cfg"
INV_FILE="${ROOT}/ansible/inventory.ini"

scripts_load_app_env "${ROOT}"
scripts_load_s3_backend_env "${ROOT}"

if [[ "${1:-}" == "ansible-playbook" ]]; then
  if [[ ! -f "${INV_FILE}" ]]; then
    echo "error: ${INV_FILE} missing (run make deploy-ci in CI or copy inventory.ini.example)" >&2
    exit 1
  fi
  scripts_export_pythonpath "${ROOT}"
  read -r inv_host inv_port inv_user < <(
    python3 - <<'PY'
import os
import sys
from pathlib import Path

from scripts.inventory import prepare_ansible_inventory

host, port, user, message = prepare_ansible_inventory(
    Path("ansible/inventory.ini"),
    expected=os.environ["PROJECT_HOSTNAME"],
)
if message:
    print(message, file=sys.stderr)
print(host, port, user)
PY
  )
  if [[ -z "${inv_host}" ]]; then
    echo "error: ansible_host is empty in ${INV_FILE}" >&2
    exit 1
  fi
  echo "ansible: ${inv_user}@${inv_host}:${inv_port} (PROJECT_HOSTNAME=${PROJECT_HOSTNAME})"
fi
exec "$@"
