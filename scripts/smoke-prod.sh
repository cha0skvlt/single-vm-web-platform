#!/usr/bin/env bash
# CI — post-deploy smoke (HTTPS + diagnostic path + Prometheus targets).
set -euo pipefail
# shellcheck source=scripts/_lib.sh
source "$(dirname "$0")/_lib.sh"
ROOT="$(scripts_repo_root)"
cd "${ROOT}"

INV_FILE="${ROOT}/ansible/inventory.ini"
if [[ ! -f "${INV_FILE}" ]]; then
  echo "error: ${INV_FILE} missing" >&2
  exit 1
fi

scripts_require_app_env "${ROOT}"

read -r DEPLOY_IP DEPLOY_PORT DEPLOY_USER < <(scripts_inventory_connection "${ROOT}")

REMOTE_APP="/opt/${PROJECT_HOSTNAME}/app"
PS_COMPOSE="docker compose --env-file .env -f sites/ops-panel/docker-compose.yml"

ssh_prod() {
  ssh -p "${DEPLOY_PORT}" -o BatchMode=yes -o StrictHostKeyChecking=yes \
    "${DEPLOY_USER}@${DEPLOY_IP}" "$@"
}

LOCAL_PROD_HOST="${PROD_PUBLIC_HOST:-}"
REMOTE_ENV="$(ssh_prod "cat '${REMOTE_APP}/.env' 2>/dev/null" || true)"
TRAEFIK_RULE="$(ssh_prod "cd '${REMOTE_APP}' && cid=\$(${PS_COMPOSE} ps -q ops-panel 2>/dev/null | head -1) && test -n \"\${cid}\" && docker inspect \"\${cid}\" --format '{{index .Config.Labels \"traefik.http.routers.ops-panel.rule\"}}'" || true)"

REMOTE_ENV_FILE="$(mktemp)"
trap 'rm -f "${REMOTE_ENV_FILE}"' EXIT
printf '%s' "${REMOTE_ENV}" > "${REMOTE_ENV_FILE}"

scripts_export_pythonpath "${ROOT}"
read -r PROD_PUBLIC_HOST < <(
  REMOTE_ENV_FILE="${REMOTE_ENV_FILE}" LOCAL_PROD_HOST="${LOCAL_PROD_HOST}" TRAEFIK_RULE="${TRAEFIK_RULE}" \
    python3 - <<'PY'
import os
import sys
from pathlib import Path

from scripts.traefik import resolve_prod_public_host

host, warnings = resolve_prod_public_host(
    remote_env=Path(os.environ["REMOTE_ENV_FILE"]).read_text(encoding="utf-8"),
    traefik_rule=os.environ.get("TRAEFIK_RULE", ""),
    local=os.environ.get("LOCAL_PROD_HOST", ""),
)
for warning in warnings:
    print(f"smoke: warning: {warning}", file=sys.stderr)
print(host)
PY
)

BASE_URL="https://${PROD_PUBLIC_HOST}"
# HTTPS checks run on the server via Traefik (127.0.0.1:443) — same path as real users, no runner egress quirks.
REMOTE_CURL=(curl -4 -sS --connect-timeout 10 --max-time 30
  --resolve "${PROD_PUBLIC_HOST}:443:127.0.0.1")

remote_curl() {
  # shellcheck disable=SC2029
  ssh_prod "$(printf '%q ' "${REMOTE_CURL[@]}")$(printf ' %q' "$@")"
}

echo "smoke: ${BASE_URL} via ${DEPLOY_IP} (HTTPS probed on host through Traefik)"

echo "smoke: waiting for ops-panel /health inside container"
ready=0
internal_health=""
for attempt in $(seq 1 45); do
  internal_health="$(ssh_prod "cd '${REMOTE_APP}' && ${PS_COMPOSE} exec -T ops-panel wget -qO- http://127.0.0.1:8080/health 2>/dev/null" || true)"
  internal_health="${internal_health//$'\r'/}"
  if [[ "${internal_health%%$'\n'*}" == "ok" ]]; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "${ready}" -ne 1 ]]; then
  echo "error: ops-panel internal /health not ready: ${internal_health:-<empty>}" >&2
  ssh_prod "cd '${REMOTE_APP}' && ${PS_COMPOSE} ps" >&2 || true
  exit 1
fi
echo "smoke: ops-panel internal /health ok"

health_body=""
health_code="000"
for attempt in $(seq 1 20); do
  health_code="$(remote_curl -o /tmp/smoke-health -w '%{http_code}' "${BASE_URL}/health" 2>/dev/null || echo "000")"
  health_body="$(ssh_prod "cat /tmp/smoke-health 2>/dev/null" || true)"
  ssh_prod "rm -f /tmp/smoke-health" 2>/dev/null || true
  health_body="${health_body//$'\r'/}"
  echo "smoke: traefik ${BASE_URL}/health attempt ${attempt} HTTP ${health_code}"
  if [[ "${health_code}" == "303" ]]; then
    break
  fi
  sleep 3
done
if [[ "${health_code}" != "303" ]]; then
  echo "error: ${BASE_URL}/health expected HTTP 303 without session, got HTTP ${health_code} body=${health_body:-<empty>}" >&2
  echo "smoke: ops-panel labels" >&2
  ssh_prod "cd '${REMOTE_APP}' && cid=\$(${PS_COMPOSE} ps -q ops-panel 2>/dev/null | head -1) && test -n \"\${cid}\" && docker inspect \"\${cid}\" --format '{{json .Config.Labels}}'" 2>/dev/null || true
  echo "smoke: traefik routers (prod)" >&2
  ssh_prod "cd '${REMOTE_APP}' && docker compose exec -T traefik wget -qO- http://172.28.10.6:8080/api/http/routers 2>/dev/null | head -c 3000" >&2 || true
  exit 1
fi

login_code="000"
for attempt in $(seq 1 10); do
  login_code="$(remote_curl -o /dev/null -w '%{http_code}' "${BASE_URL}/login" 2>/dev/null || echo "000")"
  echo "smoke: ${BASE_URL}/login attempt ${attempt} HTTP ${login_code}"
  if [[ "${login_code}" == "200" ]]; then
    break
  fi
  sleep 2
done
if [[ "${login_code}" != "200" ]]; then
  echo "error: /login HTTP ${login_code}" >&2
  exit 1
fi

diag_code="$(remote_curl -o /dev/null -w '%{http_code}' "${BASE_URL}/diagnostic" 2>/dev/null || true)"
diag_code="${diag_code//$'\r'/}"
if [[ ! "${diag_code}" =~ ^[0-9]{3}$ ]]; then
  echo "error: /diagnostic HTTP ${diag_code:-000} (expected auth redirect or 200)" >&2
  exit 1
fi
case "${diag_code}" in
  200 | 302 | 303 | 307 | 308) ;;
  *)
    echo "error: /diagnostic HTTP ${diag_code} (expected auth redirect or 200)" >&2
    exit 1
    ;;
esac

prom_down=""
prom_total=""
for attempt in $(seq 1 24); do
  prom_json="$(ssh_prod "cd '${REMOTE_APP}' && docker compose exec -T prometheus wget -qO- 'http://127.0.0.1:9090/api/v1/query?query=up' 2>/dev/null" || true)"
  read -r prom_down prom_total < <(
    printf '%s' "${prom_json}" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    print("error error")
    raise SystemExit(0)
if data.get("status") != "success":
    print("error error")
    raise SystemExit(0)
results = data.get("data", {}).get("result", [])
if not results:
    print("wait wait")
    raise SystemExit(0)
down = 0
total = 0
for series in results:
    metric = series.get("metric") or {}
    job = metric.get("job", "")
    host = metric.get("host", "")
    if job.startswith("peer") or host == "peer":
        continue
    total += 1
    value = series.get("value") or [None, "0"]
    if str(value[1]) != "1":
        down += 1
if total == 0:
    print("wait wait")
    raise SystemExit(0)
print(down, total)
'
  )
  echo "smoke: prometheus up attempt ${attempt} (${prom_total:-0} targets, ${prom_down:-?} down)"
  if [[ "${prom_down}" =~ ^[0-9]+$ && "${prom_total}" =~ ^[0-9]+$ && "${prom_down}" -eq 0 && "${prom_total}" -ge 1 ]]; then
    break
  fi
  sleep 5
done
if [[ ! "${prom_down}" =~ ^[0-9]+$ || ! "${prom_total}" =~ ^[0-9]+$ || "${prom_down}" -ne 0 || "${prom_total}" -lt 1 ]]; then
  echo "error: Prometheus targets not healthy (down=${prom_down:-?}, total=${prom_total:-?})" >&2
  ssh_prod "cd '${REMOTE_APP}' && docker compose exec -T prometheus wget -qO- 'http://127.0.0.1:9090/api/v1/targets' 2>/dev/null | head -c 2500" >&2 || true
  exit 1
fi

echo "smoke: ok (health, login, diagnostic, prometheus)"
