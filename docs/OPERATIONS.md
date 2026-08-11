# Operations and recovery

## Service objectives

The reference stage starts with these objectives; they are targets until measured in AWS.

| Objective | Target | Measurement |
| --- | ---: | --- |
| Control-plane availability | 99.9% monthly | Successful scheduled runs / eligible runs |
| Callback recovery | p95 < 3 minutes | Engine terminal time to callback completion |
| Publication freshness | p95 < 30 minutes after final branch | Active catalog timestamp |
| Lost accepted results | 0 | Signed manifest exists without accepted ledger item |
| Stale publication | 0 | Active plan differs from successful run plan |

## Routine signals

- Step Functions `ExecutionsFailed`, `ExecutionsTimedOut` and execution latency.
- DynamoDB throttles, transaction conflicts and callback age by state.
- Lambda errors, duration, concurrent executions and dead-letter destinations.
- Glue/EMR job state, DPU/vCPU hours, shuffle spill and runtime by execution profile.
- S3 Object Lock configuration drift and KMS denied operations.
- Redshift Data API failures, publication version conflicts and active-publication age.
- Airflow scheduler health, queued task age, DAG run duration and failed dependency branches.

## Recovery runbooks

### Callback remains RUNNING

1. Read the callback record consistently; record dispatch key, engine, external ID and fence.
2. Query Glue or EMR Serverless directly. Never infer success from missing events.
3. If running, send a heartbeat and preserve the token. If terminal, derive the deterministic
   manifest key, require an exact version, and send one callback.
4. If the token is absent, inspect the Step Functions execution before modifying state.
5. Never re-use an output prefix. A retry creates a new attempt.

### Lease owner disappeared

1. Confirm expiry using consistent DynamoDB read and scheduler clock health.
2. Acquire with a new owner; confirm the fence incremented.
3. Reconcile outstanding callbacks before dispatching replacements.
4. Reject or quarantine every result signed with the stale fence.

### Publication failed or timed out

1. Query `control.publication_history` and `control.active_publication` first.
2. If the requested run is active with the expected plan digest, rerun the audit job only.
3. If history exists with a different digest, stop: this is an immutable-identity incident.
4. If no row exists, retry `activate_catalog`; the stored procedure's expected version chooses a
   single winner.
5. Never repair the active table manually without a reviewed incident record and rollback plan.

### Optional branch exhausted retries

1. Confirm its `FAILURE#job#attempt` ledger records and preserve the last attempt prefix.
2. The `ALL_DONE` marker may release tolerant downstream jobs, but publication still requires every
   JobSpec marked mandatory.
3. Do not relabel a mandatory job as optional during an incident; that is a new reviewed plan.

### Manifest signature or retention check failed

1. Preserve the exact bucket, key, version and signer ARN.
2. Confirm the profile and KMS key alias in the compiled plan/deployment outputs.
3. Check Object Lock mode and retention. Do not accept an unlocked replacement object.
4. Quarantine the attempt and start a new attempt prefix after the cause is corrected.

## Rollback

Code rollback creates a new artifact digest and registry commit; it does not rewrite an existing
plan. Consumer rollback is a new, reviewed Redshift publication version referencing a previously
validated run. Destructive mutation of publication history, locked manifests or accepted ledger
items is outside normal operations.
