"""app/.env parsing and deploy payload rendering."""

from __future__ import annotations

from pathlib import Path

DEFAULT_DEPLOY_PROD_PUBLIC_HOST = "status.example.com"

REQUIRED_KEYS = (
    "LE_EMAIL",
    "GRAFANA_ADMIN_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_EXPORTER_PASSWORD",
    "EXAMPLE_DB_PASSWORD",
)

# Stable key order for APP_ENV / server .env (secrets and deploy).
DEPLOY_ENV_KEY_ORDER = (
    "PROJECT_HOSTNAME",
    "LE_EMAIL",
    "PROD_PUBLIC_HOST",
    "PROD_ADMIN_USER",
    "PROD_ADMIN_PASSWORD",
    "PROD_SESSION_SECRET",
    "GRAFANA_ADMIN_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_EXPORTER_PASSWORD",
    "EXAMPLE_DB_PASSWORD",
)


def read_env_value(text: str, key: str) -> str | None:
    """Read a single key from dotenv text without shell expansion."""
    prefix = f"{key}="
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith(prefix):
            continue
        value = stripped[len(prefix) :].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value
    return None


def parse_env_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def missing_env_keys(text: str, required: tuple[str, ...] = REQUIRED_KEYS) -> list[str]:
    present = parse_env_keys(text)
    return [k for k in required if k not in present]


def render_deploy_env(
    text: str,
    *,
    prod_public_host: str = DEFAULT_DEPLOY_PROD_PUBLIC_HOST,
) -> str:
    """Production payload: strip comments; pin PROD_PUBLIC_HOST for server/CI."""
    pairs: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        if not sep:
            continue
        pairs[key.strip()] = value.strip()
    pairs["PROD_PUBLIC_HOST"] = prod_public_host
    lines: list[str] = []
    for key in DEPLOY_ENV_KEY_ORDER:
        if key in pairs:
            lines.append(f"{key}={pairs.pop(key)}")
    for key in sorted(pairs):
        lines.append(f"{key}={pairs[key]}")
    return "\n".join(lines) + "\n"
