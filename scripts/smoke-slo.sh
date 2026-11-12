#!/usr/bin/env bash
# CI-10 — post-deploy SLO gate (Traefik 5xx ratio via Prometheus on prod).
set -euo pipefail
# shellcheck source=scripts/_lib.sh
source "$(dirname "$0")/_lib.sh"
ROOT="$(scripts_repo_root)"
cd "${ROOT}"

scripts_require_app_env "${ROOT}"
read -r DEPLOY_IP DEPLOY_PORT DEPLOY_USER < <(scripts_inventory_connection "${ROOT}")
REMOTE_APP="/opt/${PROJECT_HOSTNAME}/app"

SLO_5XX_RATIO_MAX="${SLO_5XX_RATIO_MAX:-0.05}"
SLO_MIN_REQUEST_RATE="${SLO_MIN_REQUEST_RATE:-0.001}"
SLO_WINDOW="${SLO_WINDOW:-5m}"

ssh_prod() {
  ssh -p "${DEPLOY_PORT}" -o BatchMode=yes -o StrictHostKeyChecking=yes \
    "${DEPLOY_USER}@${DEPLOY_IP}" "$@"
}

prom_query() {
  local query="$1"
  local encoded
  encoded="$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "${query}")"
  ssh_prod "cd '${REMOTE_APP}' && docker compose exec -T prometheus wget -qO- \
    'http://127.0.0.1:9090/api/v1/query?query=${encoded}' 2>/dev/null" || true
}

five_xx_query="sum(rate(traefik_router_requests_total{code=~\"5..\"}[${SLO_WINDOW}]))"
total_query="sum(rate(traefik_router_requests_total[${SLO_WINDOW}]))"

five_json="$(prom_query "${five_xx_query}")"
total_json="$(prom_query "${total_query}")"
scripts_export_pythonpath "${ROOT}"
read -r five_rate total_rate verdict detail < <(
  FIVE_JSON="${five_json}" TOTAL_JSON="${total_json}" \
  SLO_5XX_RATIO_MAX="${SLO_5XX_RATIO_MAX}" SLO_MIN_REQUEST_RATE="${SLO_MIN_REQUEST_RATE}" \
    python3 - <<'PY'
import json
import os
import sys

from scripts.smoke_slo import evaluate_5xx_ratio


def scalar(payload: str) -> float:
    if not payload.strip():
        return 0.0
    data = json.loads(payload)
    if data.get("status") != "success":
        return 0.0
    result = data.get("data", {}).get("result", [])
    if not result:
        return 0.0
    value = result[0].get("value") or [None, "0"]
    try:
        return float(value[1])
    except (TypeError, ValueError):
        return 0.0


five = scalar(os.environ["FIVE_JSON"])
total = scalar(os.environ["TOTAL_JSON"])
ok, detail = evaluate_5xx_ratio(
    five_xx_rate=five,
    total_rate=total,
    max_ratio=float(os.environ["SLO_5XX_RATIO_MAX"]),
    min_total_rate=float(os.environ["SLO_MIN_REQUEST_RATE"]),
)
print(five, total, "ok" if ok else "fail", detail)
if not ok:
    raise SystemExit(1)
PY
)

echo "smoke-slo: traefik 5xx window=${SLO_WINDOW} five=${five_rate} total=${total_rate} — ${detail} (${verdict})"

up_json="$(prom_query "sum(up == 0)")"
down_count="$(printf '%s' "${up_json}" | python3 -c '
import json, sys
raw = sys.stdin.read()
if not raw.strip():
    print("error")
    raise SystemExit(0)
data = json.loads(raw)
if data.get("status") != "success":
    print("error")
    raise SystemExit(0)
result = data.get("data", {}).get("result", [])
if not result:
    print(0)
    raise SystemExit(0)
try:
    print(int(float(result[0]["value"][1])))
except (KeyError, TypeError, ValueError):
    print("error")
')"

if [[ ! "${down_count}" =~ ^[0-9]+$ ]]; then
  echo "error: smoke-slo could not read Prometheus up==0 query" >&2
  exit 1
fi
if [[ "${down_count}" -ne 0 ]]; then
  echo "error: smoke-slo Prometheus reports ${down_count} target(s) down" >&2
  exit 1
fi

echo "smoke-slo: ok (5xx ratio + prometheus up)"
