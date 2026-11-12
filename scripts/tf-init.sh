#!/usr/bin/env bash
# Terraform init — local state by default (lab). Optional S3: pass extra args.
set -euo pipefail
# shellcheck source=scripts/_lib.sh
source "$(dirname "$0")/_lib.sh"
ROOT="$(scripts_repo_root)"
cd "${ROOT}/terraform"

scripts_load_s3_backend_env "${ROOT}"
terraform init "$@"
