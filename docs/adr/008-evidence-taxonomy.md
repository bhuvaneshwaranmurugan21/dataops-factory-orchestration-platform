# ADR 008: Classify evidence by execution boundary

Status: accepted

Repository inspection, local execution and AWS stage execution answer different questions. Claims
are labeled `VERIFIED_REPOSITORY`, `VERIFIED_LOCAL_*` or `UNVERIFIED_STAGE_REQUIRED` and validated
in CI. Local simulation may prove invariants; it cannot prove cross-account IAM, KMS retention,
managed-service recovery, throughput or cost.
