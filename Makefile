# Command contract for CI and local machines
.PHONY: help fmt validate test-quick test-service test-full \
	test-install test-install-ansible deps-gha \
	tf-init tf-plan bootstrap deploy deploy-ci deploy-ref site-slot-ci smoke smoke-slo doctor \
	ci-setup ci-key-install gh-bootstrap site-add site-rm

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
export PYTHONPATH := $(CURDIR)$(if $(PYTHONPATH),:$(PYTHONPATH),)

help:
	@echo "Quality and tests:"
	@echo "  make fmt               terraform fmt -check"
	@echo "  make validate          terraform init -backend=false && validate"
	@echo "  make test-quick        unit tests"
	@echo "  make test-service      service layer (compose, ansible, terraform, shellcheck)"
	@echo "  make test-full         test-quick + test-service"
	@echo "  make verify-ansible    ansible syntax-check + handler notify contracts"
	@echo "  make tf-init           terraform init (local state)"
	@echo "  make tf-plan           terraform plan (requires terraform.tfvars)"
	@echo "  make bootstrap         lab: key, .env, apply, inventory, ansible"
	@echo ""
	@echo "Site scaffolding:"
	@echo "  make site-add              # interactive"
	@echo "  make site-rm DOMAIN=example.ru FORCE=1"
	@echo ""
	@echo "Operator deploy (your cloud, not this repo's CI):"
	@echo "  make deploy            ansible-playbook (code only; keeps server .env)"
	@echo "  make deploy-ref REF=…  checkout REF, then deploy (rollback / break-glass)"
	@echo "  make deploy-ci         CI bootstrap + deploy (uses GitHub APP_ENV)"
	@echo "  make site-slot-ci SITE=example.ru  CI: scaffold + .env + db-provision (no ansible/tf)"
	@echo "  make smoke             post-deploy HTTPS + Prometheus (after deploy)"
	@echo "  make smoke-slo         post-deploy Traefik 5xx ratio + targets up (CI-10)"
	@echo "  make doctor            diagnostic bundle from server (LOG-3)"
	@echo ""
	@echo "One-time CI bootstrap (CICD-2, operator-run):"
	@echo "  make ci-setup          mint keys/ci_deploy (never overwrites)"
	@echo "  make ci-key-install    append CI pubkey on prod (ansible)"
	@echo "  make gh-bootstrap      rare manual .env sync (site-add with MySQL does this)"

fmt:
	terraform -chdir=terraform fmt -check -recursive

validate:
	terraform -chdir=terraform init -backend=false -input=false -reconfigure
	terraform -chdir=terraform validate

test-install:
	$(PYTHON) -m pip install -q "pytest>=8.0,<10" "PyYAML>=6.0" "Jinja2>=3.1"

test-install-ansible: test-install
	$(PYTHON) -m pip install -q "ansible-core>=2.15.13"
	@if [ -f ansible/requirements.yml ]; then \
		ansible-galaxy collection install -r ansible/requirements.yml; \
	fi

test-quick: test-install
	$(PYTHON) -m pytest tests/unit

deps-gha:
	@if [ -n "$$GITHUB_ACTIONS" ]; then \
		sudo apt-get update -qq; \
		sudo apt-get install -y -qq shellcheck; \
	fi

test-service: deps-gha test-install-ansible
	$(PYTHON) -m pytest tests/service -m service --tb=short

verify-ansible: test-install-ansible
	$(PYTHON) -m pytest tests/service/test_ansible.py tests/service/test_ansible_handlers.py -m service --tb=short

test-full: test-quick test-service

tf-init:
	scripts/tf-init.sh

tf-plan:
	scripts/tf-plan.sh

bootstrap:
	scripts/bootstrap.sh

deploy-ci:
	scripts/ci-deploy-bootstrap.sh
	$(MAKE) verify-ansible
	scripts/with-project-env.sh ansible-playbook -i ansible/inventory.ini ansible/playbook.yml -e app_env_push=true

deploy: test-install-ansible
	scripts/with-project-env.sh ansible-playbook -i ansible/inventory.ini ansible/playbook.yml

deploy-ref:
	@test -n "$(REF)" || { echo "REF= required (commit SHA or branch)"; exit 1; }
	git fetch origin "$(REF)"
	git checkout "$(REF)"
	$(MAKE) deploy

site-slot-ci:
	@test -n "$(SITE)" || { echo "SITE= required (FQDN)"; exit 1; }
	scripts/ci-deploy-bootstrap.sh
	scripts/site-slot-provision.sh --site "$(SITE)"

smoke:
	scripts/with-project-env.sh scripts/smoke-prod.sh

smoke-slo:
	scripts/with-project-env.sh scripts/smoke-slo.sh

doctor:
	scripts/with-project-env.sh python3 -m scripts.diagnostic --fetch

ci-setup:
	scripts/ci-keygen.sh
	@echo ""
	@echo "Next steps (manual, non-delegable):"
	@echo "  1. make ci-key-install    # SSH to the VM; append CI pubkey"
	@echo "  2. FIP=<floating-ip> make gh-bootstrap   # or set ansible/inventory.ini first"
	@echo "CI key is separate from keys/deploy.pub (operator); revoke by removing ci_deploy.pub on the VM."

ci-key-install: test-install-ansible
	@test -f keys/ci_deploy.pub || { echo "run make ci-setup first"; exit 1; }
	scripts/with-project-env.sh ansible-playbook -i ansible/inventory.ini ansible/ci-key.yml

gh-bootstrap:
	scripts/gh-bootstrap.sh

site-add:
	$(PYTHON) -m scripts.cli site-add --interactive

site-rm:
	@test -n "$(DOMAIN)" || { echo "DOMAIN= required"; exit 1; }
	@test "$(FORCE)" = "1" || { echo "FORCE=1 required"; exit 1; }
	$(PYTHON) -m scripts.cli site-rm "$(DOMAIN)" --force
