# Architecture decision records

| ADR | Decision |
| --- | --- |
| [001](001-split-scheduling-and-execution.md) | Airflow schedules; Step Functions owns one workload |
| [002](002-compile-immutable-plans.md) | Compile a strict registry into an immutable plan |
| [003](003-lease-and-fencing.md) | Use leases plus monotonic fencing tokens |
| [004](004-signed-locked-manifests.md) | Accept exact-version, signed and locked manifests |
| [005](005-redshift-publication-authority.md) | Make Redshift the transactional consumer authority |
| [006](006-authoritative-reconciliation.md) | Reconcile from service APIs, not event delivery |
| [007](007-attempt-isolation.md) | Isolate every attempt's outputs |
| [008](008-evidence-taxonomy.md) | Classify repository, local and cloud evidence |
