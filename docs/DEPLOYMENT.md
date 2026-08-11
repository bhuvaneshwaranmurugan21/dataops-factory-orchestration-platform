# AWS stage deployment gate

## Prerequisites

- Five AWS accounts with a pre-existing `dataops-terraform-deployer` bootstrap role.
- AWS Organizations/SCP review and non-production billing alarms.
- Private networking and endpoints reviewed for the selected region.
- Python 3.11, Terraform 1.8+, AWS CLI and credentials allowed to assume the bootstrap roles.
- Airflow with the Amazon provider and access to the control-account registration Lambda and state
  machine.

## Build and validate

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,aws]"
make evidence
make verify
make package
terraform -chdir=infrastructure/terraform init -backend=false
terraform -chdir=infrastructure/terraform validate
```

Review `dist/*/checksums.json`. The packaging scripts fix ZIP entry ordering, timestamps and modes;
the orchestration CI gate builds twice and requires identical checksums.

## Plan and apply

Copy `terraform.tfvars.example` to an untracked `terraform.tfvars` and replace every account ID.
Use a reviewed remote backend for shared work.

```bash
terraform -chdir=infrastructure/terraform plan \
  -var-file=terraform.tfvars \
  -out=stage.tfplan
terraform -chdir=infrastructure/terraform apply stage.tfplan
```

Before approval, inspect every trust policy, KMS key policy, Object Lock setting, public-access block,
runtime target and `iam:PassRole` condition. A successful plan is not deployment evidence.

## Bootstrap publication schema

```bash
python scripts/bootstrap_redshift.py \
  --workgroup "$(terraform -chdir=infrastructure/terraform output -raw redshift_workgroup_name)" \
  --database "$(terraform -chdir=infrastructure/terraform output -raw redshift_database)" \
  --secret-arn "$(terraform -chdir=infrastructure/terraform output -raw redshift_admin_secret_arn)"
```

The script submits the schema, history table, active pointer and atomic activation procedure as one
Redshift Data API batch and fails on an aborted, failed or timed-out statement.

The reference stage uses the namespace-managed admin secret for bootstrap and the narrowly scoped
publication Lambda. Before production promotion, replace this with a dedicated database principal
that has only schema usage, table read/write and procedure execute privileges.

## Configure Airflow

Set these environment variables for the scheduler/workers:

- `DATAOPS_WORKLOAD_STATE_MACHINE_ARN`
- `DATAOPS_REGISTER_RUN_FUNCTION`
- `DATAOPS_MARK_FAILED_FUNCTION`

Deploy both `orchestration/generated/compiled-plan.json` and
`orchestration/dags/dataops_factory.py` from the same reviewed commit. Do not compile dynamically in
the scheduler.

## Promotion checks

1. Execute one job through each adapter in every workload account.
2. Confirm exact-version Object Lock and KMS signature validation.
3. Drop or delay a terminal engine notification and confirm reconciler recovery.
4. Expire a lease while a worker completes; confirm the stale manifest is rejected.
5. Race two publications; confirm one Redshift version wins and history remains consistent.
6. Execute the performance protocol in `PERFORMANCE.md`.
7. Export CloudTrail, CloudWatch, DynamoDB, Step Functions, Spark and Redshift evidence.
8. Add a classified `evidence/verified-stage/` bundle in a reviewed change.

Only then may stage claims move out of `UNVERIFIED_STAGE_REQUIRED`.
