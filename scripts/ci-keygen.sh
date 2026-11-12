#!/usr/bin/env bash
# CICD-2 — mint dedicated passphraseless CI deploy key (never overwrites).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${ROOT}/keys/ci_deploy"
PUB="${KEY}.pub"

if [[ -f "${KEY}" ]]; then
  echo "ci-keygen: ${KEY} already exists (not overwriting)"
  echo "public key: ${PUB}"
  exit 0
fi

mkdir -p "${ROOT}/keys"
ssh-keygen -t ed25519 -N "" -C "ci-deploy@github-actions" -f "${KEY}"
chmod 600 "${KEY}"
chmod 644 "${PUB}"
echo "ci-keygen: created ${KEY}"
echo "public key: ${PUB}"
