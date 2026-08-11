# Architecture

## Scope

DataOps Factory is a metadata-driven orchestration control plane. Its job is to compile, dispatch,
observe, validate and publish heterogeneous data workloads. The sample Spark transformations prove
the adapter contracts; domain-specific business transformations remain independently deployable
artifacts identified by immutable SHA-256 digests.

## Planes and authorities

| Plane | Authority | Responsibility |
| --- | --- | --- |
| Definition | Versioned registry | Job identity, dependencies, policies, artifact digests |
| Compilation | Deterministic compiler | Validation, topological levels, immutable plan digest |
| Scheduling | Airflow | Calendar, dependency release, bounded active runs |
| Workload | Step Functions Standard | One callback lifecycle, timeout and failure boundary |
| Run state | DynamoDB | Run lease, fence, callbacks, accepted manifests |
| Evidence | S3 Object Lock + KMS | Exact immutable result-manifest versions |
| Publication | Redshift | Transactional active consumer pointer and history |
| Recovery | Reconciler | Accelerates convergence from managed-engine truth |

No event is treated as truth merely because it arrived. Lambda completion is synchronous; Glue and
EMR Serverless completion is recovered by polling their authoritative APIs. EventBridge starts the
reconciler, but correctness does not depend on one delivery.

## Compilation and execution

1. The compiler rejects malformed fields, unsafe retry combinations, unknown dependencies and
   cycles, then emits stable topological levels and a canonical plan digest.
2. Airflow registers the logical run. DynamoDB binds its logical key to the immutable plan and
   leases it with a monotonically increasing fence.
3. Airflow releases a job only after all declared parents succeed.
4. Step Functions stores the callback token through the dispatcher before a workload can start.
5. The dispatcher resolves a reviewed execution profile, assumes only that profile's role and calls
   Lambda, Glue or EMR Serverless with a deterministic dispatch identity.
6. The worker writes only beneath its attempt-and-fence prefix, emits a KMS-signed manifest and
   protects the exact manifest version with Object Lock.
7. The validator checks the S3 version, retention state, signer, digest and every immutable semantic
   binding, then conditionally accepts the result against the live DynamoDB fence.

## Publication

`activate_catalog` uses a dedicated adapter. Before touching Redshift it consistently queries the
accepted-job set and fails if any of the 32 mandatory predecessors is absent. Optional jobs close
through explicit `ALL_DONE` markers and cannot hide a mandatory failure. A Redshift stored
procedure locks the active-publication table, rejects a stale expected version, inserts immutable
history and changes the consumer pointer in one transaction. `audit_publication` then reads the
active pointer independently and emits its own signed evidence.

The DynamoDB acceptance ledger and Redshift consumer catalog are deliberately not presented as one
distributed transaction. Redshift is consumer authority; the signed audit result proves the
observed pointer. The local oracle also injects a crash after catalog commit and proves idempotent
reconciliation.

## Scaling boundaries

- Airflow expands 36 static tasks from a checked-in plan, so scheduling is inspectable and bounded.
- Step Functions executions are one-workload units, avoiding one giant state-machine history.
- Callback records are keyed by deterministic dispatch identity and swept in bounded pages.
- DynamoDB uses on-demand capacity and narrow items; output data never enters the control ledger.
- Result manifests carry references and measurements, not datasets.
- Glue handles standard Spark workloads; EMR Serverless profiles isolate heavy shuffle workloads.

## Deliberate boundaries

- The local HMAC signer models only the signature contract; AWS uses asymmetric KMS keys.
- SQLite is the executable correctness oracle for lease/publication behavior, not an AWS emulator.
- The checked-in architecture is deployable infrastructure, not proof of a deployed environment.
- Performance targets become evidence only after the stage benchmark protocol is executed.
