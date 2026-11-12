# OpenStack Terraform module

Headline layer of this sample. One root module on the OpenStack provider, with a Selectel VPC profile ready to use.

## Provider

`terraform/versions.tf` pins `terraform-provider-openstack/openstack` 2.1.0 and Terraform >= 1.10. The provider block reads `auth_url`, `user_domain_name`, `project_id`, `username`, and `password` from variables, so the same module targets any OpenStack cloud.

Selectel VPC speaks OpenStack Identity at `https://cloud.api.selcloud.ru/identity/v3`; `terraform/examples/selectel.tfvars.example` fills that endpoint and the `ru-3` placement.

## Resources

- Private network + subnet + router to the external net
- Security group: SSH (`var.ssh_port`, default 22), HTTP, HTTPS
- Optional Loki ingest `:3100` from `var.peer_host_cidr` (omitted when empty)
- Floating IP + port
- Boot volume + compute instance
- Key pair from `keys/deploy.pub` (placeholder; `make bootstrap` writes your key)
- cloud-init: user `deploy`, SSH hardening, key-only login

## Variables

| Variable | Notes |
| --- | --- |
| `auth_url` | Keystone |
| `username` / `password` | Sensitive |
| `user_domain_name` | Selectel: account ID |
| `project_id` or `tenant_name` | Project |
| `region` / `az` | Placement |
| `image_name` / `flavor_id` / `volume_type` | Cloud-specific |
| `external_network_name` | Empty = first `external=true` network |
| `ssh_port` | Default 22 |
| `volume_metadata` | Optional IOPS caps (Selectel lab sets these) |
| `peer_host_cidr` | Optional `/32` for Loki push |

## Selectel profile

`terraform/examples/selectel.tfvars.example` pins:

- Identity `https://cloud.api.selcloud.ru/identity/v3`
- `ru-3` / `ru-3b`
- Image `Debian 13 (Trixie) 64-bit`
- Flavor `1054` (4 vCPU / 8 GB)
- Volume type `universal2.ru-3b`

The module is provider-generic; this profile is a ready starting point. Other clouds use `generic.tfvars.example`.

## State

Default: local state (lab). Optional S3-compatible backend: `backend.s3.hcl.example`.
