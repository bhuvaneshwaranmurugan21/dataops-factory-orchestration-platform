.PHONY: verify compile simulate failure-lab evidence format package terraform-validate

verify:
	ruff check src tests lambdas orchestration scripts spark_jobs
	ruff format --check src tests lambdas orchestration scripts spark_jobs
	mypy src/dataops_factory
	pytest
	python scripts/validate_claims.py
	python scripts/validate_architecture_contract.py

compile:
	dataops-factory compile --registry contracts/registry/jobs.json --output artifacts/compiled-plan.json

simulate:
	dataops-factory simulate --registry contracts/registry/jobs.json --work-dir artifacts/simulation

failure-lab:
	dataops-factory failure-lab --registry contracts/registry/jobs.json --work-dir artifacts/failure-lab

evidence:
	python scripts/refresh_evidence.py

package:
	python scripts/package_lambdas.py
	python scripts/package_spark_jobs.py

format:
	ruff check --fix src tests lambdas orchestration scripts spark_jobs
	ruff format src tests lambdas orchestration scripts spark_jobs
	terraform fmt -recursive infrastructure/terraform

terraform-validate:
	terraform -chdir=infrastructure/terraform init -backend=false
	terraform -chdir=infrastructure/terraform validate
