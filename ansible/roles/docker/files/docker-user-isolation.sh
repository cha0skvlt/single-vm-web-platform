#!/bin/bash
# Idempotent DOCKER-USER chain: optional Loki :3100 allowlist; drop web→admin ports.
set -euo pipefail

CHAIN="${DOCKER_USER_CHAIN:-DOCKER-USER-PLATFORM}"
PEER="${PEER_LOKI_SOURCE_IP:-}"
WEB_NET="${DOCKER_WEB_NETWORK:-web}"
DROP_PORTS="${DOCKER_USER_DROP_TCP_PORTS:-8080 9090 9100}"
DROP_PORTS="${DROP_PORTS//,/ }"

if ! iptables -nL DOCKER-USER >/dev/null 2>&1; then
  echo "DOCKER-USER chain missing; skip" >&2
  exit 0
fi

iptables -N "$CHAIN" 2>/dev/null || iptables -F "$CHAIN"

if ! iptables -C DOCKER-USER -j "$CHAIN" 2>/dev/null; then
  iptables -I DOCKER-USER 1 -j "$CHAIN"
fi

if [ -n "$PEER" ]; then
  iptables -A "$CHAIN" -p tcp --dport 3100 -s "$PEER" -j RETURN
  iptables -A "$CHAIN" -p tcp --dport 3100 -j DROP
fi

iptables -A "$CHAIN" -p tcp --dport 9323 -s 172.16.0.0/12 -j RETURN
iptables -A "$CHAIN" -p tcp --dport 9323 -j DROP

WEB_SUBNET=""
if command -v docker >/dev/null 2>&1; then
  WEB_SUBNET="$(docker network inspect "$WEB_NET" --format '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null || true)"
fi
if [ -n "$WEB_SUBNET" ]; then
  # shellcheck disable=SC2086
  for port in $DROP_PORTS; do
    iptables -A "$CHAIN" -s "$WEB_SUBNET" -p tcp --dport "$port" -j DROP
  done
fi
