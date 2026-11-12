#!/usr/bin/env bash
# Lab bootstrap: SSH key, .env, terraform apply (local state), inventory, ansible.
# Intended for Selectel (or any OpenStack after terraform/terraform.tfvars exists).
set -euo pipefail
# shellcheck source=scripts/_lib.sh
source "$(dirname "$0")/_lib.sh"
ROOT="$(scripts_repo_root)"
cd "${ROOT}"

die() { echo "error: $*" >&2; exit 1; }

tfvars_password_set() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  python3 - "$f" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
for line in text.splitlines():
    s = line.strip()
    if s.startswith("password") and "=" in s:
        val = s.split("=", 1)[1].strip().strip('"').strip("'")
        sys.exit(0 if val else 1)
sys.exit(1)
PY
}

# Placeholder keys/deploy.pub exists for terraform validate. Without the
# matching private key the lab VM would accept a key nobody holds.
mkdir -p keys
if [[ ! -f keys/deploy ]]; then
  rm -f keys/deploy.pub
  ssh-keygen -t ed25519 -N "" -C "deploy@single-vm-web-platform" -f keys/deploy
  echo "bootstrap: minted keys/deploy (overwrote placeholder .pub if present)"
fi

if [[ ! -f app/.env ]]; then
  cp app/.env.example app/.env
  python3 - "${ROOT}/app/.env" <<'PY'
import secrets
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
replacements = {
    "PROD_ADMIN_PASSWORD=__CHANGE_ME__": "PROD_ADMIN_PASSWORD=" + secrets.token_urlsafe(18),
    "PROD_SESSION_SECRET=__CHANGE_ME_GENERATE_WITH_openssl_rand_hex_32__": "PROD_SESSION_SECRET=" + secrets.token_hex(32),
    "GRAFANA_ADMIN_PASSWORD=__CHANGE_ME__": "GRAFANA_ADMIN_PASSWORD=" + secrets.token_urlsafe(18),
    "MYSQL_ROOT_PASSWORD=__CHANGE_ME__": "MYSQL_ROOT_PASSWORD=" + secrets.token_urlsafe(24),
    "MYSQL_EXPORTER_PASSWORD=__CHANGE_ME__": "MYSQL_EXPORTER_PASSWORD=" + secrets.token_urlsafe(24),
    "EXAMPLE_DB_PASSWORD=__CHANGE_ME__": "EXAMPLE_DB_PASSWORD=" + secrets.token_urlsafe(24),
    "EXAMPLE_APP_KEY=__CHANGE_ME__": "EXAMPLE_APP_KEY=base64:" + secrets.token_urlsafe(32),
}
for old, new in replacements.items():
    text = text.replace(old, new)
path.write_text(text, encoding="utf-8")
path.chmod(0o600)
PY
  echo "bootstrap: wrote app/.env with generated passwords"
fi

TFVARS="${ROOT}/terraform/terraform.tfvars"
if [[ ! -f "${TFVARS}" ]]; then
  cp terraform/examples/selectel.tfvars.example "${TFVARS}"
  echo "bootstrap: copied terraform/examples/selectel.tfvars.example → terraform/terraform.tfvars"
  echo "Fill user_domain_name, project_id, username, password — then re-run make bootstrap."
  exit 1
fi

if ! tfvars_password_set "${TFVARS}"; then
  die "terraform/terraform.tfvars has an empty password. Fill Selectel (or OpenStack) credentials."
fi

scripts_load_app_env "${ROOT}"
HOSTNAME_ID="${PROJECT_HOSTNAME:-prod}"

(
  cd terraform
  terraform init -input=false
  if [[ "${BOOTSTRAP_AUTO:-}" == "1" ]]; then
    terraform apply -input=false -auto-approve
  else
    terraform apply -input=false
  fi
)

FIP="$(terraform -chdir=terraform output -raw public_ip_address)"
SSH_PORT="$(terraform -chdir=terraform output -raw ssh_port)"
[[ -n "${FIP}" ]] || die "terraform output public_ip_address is empty"

python3 - "${ROOT}" "${HOSTNAME_ID}" "${FIP}" "${SSH_PORT}" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.inventory import render_inventory_ini
root, host, fip, port = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
path = Path(root) / "ansible" / "inventory.ini"
path.write_text(render_inventory_ini(hostname=host, ansible_host=fip, ansible_port=port), encoding="utf-8")
path.chmod(0o600)
print(f"bootstrap: wrote ansible/inventory.ini [{host}] {fip}:{port}")
PY

echo "bootstrap: waiting for ssh deploy@${FIP}:${SSH_PORT}"
ssh_ready=0
for _ in $(seq 1 60); do
  if ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new \
    -p "${SSH_PORT}" -i keys/deploy "deploy@${FIP}" true 2>/dev/null; then
    echo "bootstrap: ssh ready"
    ssh_ready=1
    break
  fi
  sleep 5
done
if [[ "${ssh_ready}" -ne 1 ]]; then
  die "ssh deploy@${FIP}:${SSH_PORT} not ready after ~5m (cloud-init or security group). Inventory is written; fix SSH then: make deploy"
fi

scripts/with-project-env.sh ansible-playbook -i ansible/inventory.ini ansible/playbook.yml

PANEL_HOST="${PROD_PUBLIC_HOST:-status.example.com}"
echo ""
echo "Bootstrap finished."
echo "  Floating IP: ${FIP}"
echo "  SSH:         ssh -p ${SSH_PORT} -i keys/deploy deploy@${FIP}"
echo "  DNS:         A ${PANEL_HOST} → ${FIP}"
echo "  Panel:       https://${PANEL_HOST}  (Host rule + Let's Encrypt; not https://${FIP})"
echo "  Next:        make site-add"
echo "  Teardown:    terraform -chdir=terraform destroy"
