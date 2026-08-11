# Performance and benchmark protocol

## Evidence status

The repository proves deterministic compilation, bounded orchestration contracts and local Spark
transformation invariants. It does not claim AWS throughput. Numbers below are admission targets
for a stage test; measured results belong under `evidence/verified-stage/` with the deployment ID,
region, service configuration, dataset generator seed and CloudWatch query attached.

## Capacity model

For one complete run:

- 36 workload state-machine executions are created across 12 dependency levels.
- The widest reviewed level contains 13 jobs.
- The registry routes 18 Lambda, 14 Glue and 4 EMR Serverless workloads.
- Each accepted job creates one locked manifest and one narrow DynamoDB acceptance item.
- Reconciliation scans only RUNNING callback keys and caps each invocation at 100 records.

At four active Airflow runs, the theoretical scheduling envelope is 144 workload executions, but
the instantaneous service demand depends on level overlap and account quotas. The stage gate must
therefore validate Lambda concurrency, Step Functions starts, Glue concurrent runs, EMR Serverless
capacity, STS request rate, KMS signing rate and Redshift Data API limits before increasing
`max_active_runs`.

## Stage acceptance targets

| Signal | Admission target |
| --- | ---: |
| Dispatch p95 excluding workload runtime | < 2 seconds |
| Terminal-engine state to callback p95 | < 3 minutes |
| Manifest validation p95 | < 1 second |
| DynamoDB transaction throttles | 0 |
| Duplicate accepted financial effects | 0 |
| Publication CAS conflicts | Explained by intentional concurrency only |
| Control-plane error rate | < 0.1% over 1,000 workload executions |
| Recovery completion after injected event loss | 100% within two sweeps |

## Benchmark phases

1. **Warm path:** one run, no injected faults; record cold starts and first-use credentials.
2. **Steady path:** 10 complete DAG runs at concurrency one; establish latency/cost baseline.
3. **Concurrency:** 25 complete runs at active-run settings 1, 2 and 4.
4. **Quota pressure:** progressively increase only one adapter class at a time.
5. **Failure recovery:** suppress terminal notifications, expire a lease, duplicate callbacks and
   race two publications.
6. **Soak:** six hours at the intended schedule, with callback and publication age alarms enabled.

Every phase must retain raw start/stop timestamps, configuration, failure injection, CloudWatch
metric export and cost allocation tags. A percentile without the sample count and workload profile
is not accepted as evidence.

The executable workload-stage harness registers unique fenced runs, drives the real state machine
with bounded concurrency, waits for terminal status, and writes per-execution latency and status:

```bash
python scripts/benchmark_stage.py \
  --register-function "$DATAOPS_REGISTER_RUN_FUNCTION" \
  --state-machine-arn "$DATAOPS_WORKLOAD_STATE_MACHINE_ARN" \
  --fixture config/stage-benchmark-job.example.json \
  --executions 100 \
  --concurrency 4 \
  --output evidence/verified-stage/lambda-stage-100.json
```

## Spark benchmark rules

- Fix Glue version, worker type/count, EMR release label, capacity and Spark configuration.
- Generate deterministic datasets with recorded seed, schema, file count, compression and total
  bytes. Report both logical and compressed size.
- Record input rows/bytes, output rows/bytes, runtime, DPU/vCPU hours, shuffle read/write, spill,
  skew distribution and failed-task retries.
- Compare standard Glue and heavy EMR profiles only on the same transformation and dataset.
- Perform at least five measured repetitions after one warm-up; report median and p95.

## Promotion rule

Do not change `UNVERIFIED_STAGE_REQUIRED` claims merely because Terraform applied. Promotion needs
a successful smoke run, fault recovery, benchmark output and a reviewer-verifiable evidence bundle.
