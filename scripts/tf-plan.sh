#!/usr/bin/env bash
# Terraform plan. Skips when terraform.tfvars is absent (CI without cloud creds).
set -euo pipefail
# shellcheck source=scripts/_lib.sh
source "$(dirname "$0")/_lib.sh"
ROOT="$(scripts_repo_root)"
cd "${ROOT}/terraform"

if [[ ! -f terraform.tfvars ]] && [[ -z "${TF_VAR_password:-}" ]]; then
  echo "terraform plan skipped: terraform.tfvars not found and TF_VAR_password not set"
  exit 0
fi

scripts_load_s3_backend_env "${ROOT}"
terraform init -input=false
terraform plan -no-color -input=false | tee plan.txt
