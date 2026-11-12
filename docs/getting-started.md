# Getting started

## Selectel lab

1. Create a Selectel VPC project and an API user with project scope.
2. Clone this repo.
3. Copy the lab profile and fill four fields:

```bash
cp terraform/examples/selectel.tfvars.example terraform/terraform.tfvars
```

Required: `user_domain_name` (account ID), `project_id`, `username`, `password`. Image, flavor, region, and volume type are already set for a typical `ru-3` lab.

4. Run:

```bash
make bootstrap
```

Idempotent. With `terraform/terraform.tfvars` missing, the script copies the example and exits so you can fill it; with an empty password it stops with a message. With credentials it:

- mints `keys/deploy` if missing
- writes `app/.env` with generated passwords if missing
- `terraform init` + `apply` (local state; prompt unless `BOOTSTRAP_AUTO=1`)
- writes `ansible/inventory.ini` from the floating IP
- waits for `deploy@FIP` SSH
- runs the Ansible playbook

5. Point a DNS A record for `PROD_PUBLIC_HOST` (default `status.example.com`) at the printed floating IP. The panel answers on `https://<PROD_PUBLIC_HOST>`: Traefik matches the Host header, and Let’s Encrypt issues certificates for DNS names.
6. SSH: `ssh -p <port> -i keys/deploy deploy@<FIP>`.
7. `make site-add` — see [sites.md](sites.md).

Teardown: `terraform -chdir=terraform destroy` removes the VM, boot volume, and floating IP.

## Other OpenStack

Same script after:

```bash
cp terraform/examples/generic.tfvars.example terraform/terraform.tfvars
```

Fill `auth_url`, `region`, `az`, `image_name`, `flavor_id`, `volume_type`, and identity fields. See [openstack.md](openstack.md).

## Remote state

The lab uses local `terraform/terraform.tfstate` (gitignored). For a lasting environment, add an S3 backend with `terraform/backend.s3.hcl.example` and `terraform init -backend-config=...`. `make bootstrap` runs on local state.
