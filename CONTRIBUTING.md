# Contributing

This repository is a source-available portfolio sample under [PolyForm Shield 1.0.0](LICENSE). Issues and PRs that fix sanitization leaks, tests, or docs are welcome. The project ships as a reference sample.

```bash
make test-full
make fmt
make validate
```

Keep `app/.env`, `terraform.tfvars`, state files, PEMs, and client domains out of git. A sanitization sweep keeps the tree clean: `bioline`, `bioprod`, `biogit`, production IPs — the documented Selectel example under `terraform/examples/` is the exception.
