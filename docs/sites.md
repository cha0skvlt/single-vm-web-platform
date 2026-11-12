# Sites

## Add a site

```bash
make site-add
```

Prompts: domain → PHP? (8.2 / 8.4) → MySQL? → else Node (Nuxt)? → Let’s Encrypt?

Scaffolds `app/sites/<fqdn>/` from `_example`, `_example_php`, `_example_php82`, or `_example_nuxt`. MySQL name/user come from the domain (`example.com` → `example_com`). Passwords and `APP_KEY` are generated into `app/.env`.

DNS lives at the registrar: A record → floating IP (`terraform output -raw public_ip_address`).

## Ship the slot

Rsync `app/sites/<fqdn>/` and `app/.env` to the VM (or your own CI). On the server, `scripts/db-provision.sh --site <fqdn>` creates the database when MySQL was enabled. Full platform `make deploy` also runs `db-provision.sh --all`.

## Application code

Paste the generated `app/sites/<fqdn>/site-repo/.gitlab-ci.yml` into the site repo. Set `DEPLOY_HOST` to the floating IP. Store `CI_DEPLOY_KEY` as a CI variable. A push runs the pipeline; the `deploy_prod` job is manual (▶) and performs rsync + `deploy-site.sh`.

`deploy-site.sh` uses a per-site Compose project name, so `--remove-orphans` stays inside one site.

## Remove

```bash
make site-rm DOMAIN=example.com FORCE=1
```

Then deploy the platform once so the host matches the repo.
