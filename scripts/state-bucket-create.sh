#!/usr/bin/env bash
# One-time helper: create the Terraform state bucket on Selectel S3 and enable versioning.
# Requires aws-cli and AWS_* creds for a dedicated state key (see terraform/s3-backend.env.example).
set -euo pipefail
# shellcheck source=scripts/_lib.sh
source "$(dirname "$0")/_lib.sh"
ROOT="$(scripts_repo_root)"
BUCKET="${TF_STATE_BUCKET:-web-platform-tfstate}"
REGION="${TF_STATE_REGION:-ru-3}"
ENDPOINT="https://s3.${REGION}.storage.selcloud.ru"

scripts_load_s3_backend_env "${ROOT}"

if [[ -z "${AWS_ACCESS_KEY_ID:-}" || -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
  echo "error: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (e.g. via terraform/s3-backend.env)" >&2
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "error: aws-cli not found" >&2
  exit 1
fi

export AWS_DEFAULT_REGION="${REGION}"

if aws s3api head-bucket --bucket "${BUCKET}" --endpoint-url "${ENDPOINT}" 2>/dev/null; then
  echo "bucket ${BUCKET} already exists"
else
  aws s3api create-bucket --bucket "${BUCKET}" --endpoint-url "${ENDPOINT}"
  echo "created bucket ${BUCKET}"
fi

aws s3api put-bucket-versioning \
  --bucket "${BUCKET}" \
  --endpoint-url "${ENDPOINT}" \
  --versioning-configuration Status=Enabled
echo "versioning enabled on ${BUCKET}"
