# ADR 004: Accept signed, exact-version manifests

Status: accepted

Completion events carry references, not trust. Workers write a canonical manifest, sign its digest
with an account-bound asymmetric KMS key and store it in a versioned Object Lock bucket. Acceptance
fetches the exact version and binds content to the scheduled run, plan, job, attempt, fence, artifact,
account, quality policy, cost budget and output prefix.
