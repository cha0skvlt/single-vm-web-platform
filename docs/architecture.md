# Architecture

Layers:

| Layer | Path | Role |
| --- | --- | --- |
| Cloud | `terraform/` | Network, VM, floating IP, SSH |
| Host | `ansible/` | OS, UFW, Docker, fail2ban, rsync of the stack |
| Platform | `app/docker-compose.yml` | Traefik, MySQL, observability |
| Panel | `app/sites/ops-panel/` | Login, health, forwardAuth |
| Sites | `app/sites/<fqdn>/` | One Compose file per site |

## Delivery planes

- **Platform** — this repo. Terraform + Ansible + base stack + panel. GitHub Actions run tests and validation; deployment runs from the operator's machine.
- **Site application** — a separate Git project. The runner rsyncs files and runs `deploy-site.sh --site <fqdn>`. Terraform and Ansible stay in this repo, under the operator.

`PROJECT_HOSTNAME` (default `prod`) is the install id: inventory group and `/opt/<name>/`. Terraform `hostname` is the OpenStack name prefix (default `web`).

## Networks

- `web` — site containers + Traefik
- `platform` — Grafana, Loki, Alloy, panel (operator plane, isolated from `web`)
- `db` — shared MySQL; PHP sites join when they need a database

Traefik reaches the Docker API through `docker-socket-proxy`.

## TLS

Site routers default to Let’s Encrypt (`tls.certresolver=le`) after the public A record points at the floating IP. Purchased PEMs go under `app/certs/<domain>/` (gitignored).

## Observability

Prometheus scrapes Traefik, node-exporter, Docker engine metrics, mysqld-exporter, and site exporters via Docker SD on `web`. Alloy ships container logs, journald, and fail2ban to Loki. Grafana folder **Platform**.

## Secrets

Committed: `app/.env.example`, `terraform/examples/*.tfvars.example`, `keys/deploy.pub` (placeholder).  
Gitignored: `app/.env`, `terraform.tfvars`, `*.tfstate`, `ansible/inventory.ini`, private keys, PEMs.

## Operational notes

- CI runs `terraform validate`; `apply` runs from the operator's machine
- First boot is cloud-init
- `make deploy` keeps an existing server `.env`; pass `app_env_push=true` to overwrite it
