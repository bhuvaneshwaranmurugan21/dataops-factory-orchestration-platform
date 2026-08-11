# Threat model

## Assets

- Immutable registry commit and compiled plan digest.
- Run lease, fencing token, callback token and accepted-manifest set.
- Workload artifacts and per-attempt output objects.
- KMS signing keys, locked manifest versions and publication history.
- Cross-account execution roles and Redshift active consumer pointer.

## Trust boundaries

1. Git/CI to packaged artifacts.
2. Airflow to the AWS control account.
3. Control account to four workload accounts through reviewed STS profiles.
4. Workload runtimes to S3/KMS result evidence.
5. Validated manifests to DynamoDB acceptance.
6. Accepted fan-in to Redshift publication.

## Threats and controls

| Threat | Control | Residual risk / stage test |
| --- | --- | --- |
| Registry or artifact substitution | Canonical plan and artifact SHA-256 binding | Protect main branch and release process |
| Arbitrary role or target dispatch | Profile allowlist; no request-supplied ARN | Validate IAM policy analyzer findings |
| Confused deputy | STS external ID is deterministic dispatch key | Confirm CloudTrail condition behavior |
| Stale scheduler mutation | Lease expiry plus monotonically increasing fence | Test real concurrent schedulers |
| Callback theft/replay | Token stored before dispatch; removed after callback | Encrypt table, restrict reads, test duplicate callback |
| Output overwrite | Attempt-and-fence S3 prefix; error-if-exists writes | Lifecycle policy must not erase active evidence |
| Manifest tampering | Exact S3 version, Object Lock, digest, asymmetric KMS signature | Test retention and key policy in every account |
| Cross-account manifest swap | Bucket, signer and account bound to execution profile | Test access denied across account pairs |
| Quality/budget downgrade | Policies and budget are immutable plan inputs | Domain checks must be independently reviewed |
| Publication race | Locked Redshift CAS procedure and immutable history | Inject concurrent activation in stage |
| Privilege expansion | Scoped roles, service principals, project tags, PassRole condition | Run IAM Access Analyzer before promotion |
| Sensitive log disclosure | Step Functions excludes execution data; no callback tokens in logs | Confirm sampling and exception redaction |
| Dependency compromise | Pinned tools, pip audit, deterministic packages | Add signed releases/SBOM before production |
| Publication credential compromise | Managed encrypted secret; access limited to publication role | Replace admin with procedure-only database principal before production |

## Security invariants

- A request never supplies an executable role ARN, KMS key ARN, bucket or runtime target.
- A manifest is not accepted unless both cryptographic and semantic bindings pass.
- Object Lock absence is a hard failure.
- KMS signing uses asymmetric `SIGN_VERIFY`; encryption keys cannot impersonate signers.
- Publication history is append-only in normal operations.
- Local HMAC evidence is never represented as AWS KMS evidence.

## Not yet production-complete

The stage must add organization-specific SCPs, VPC endpoints, CloudTrail data events, GuardDuty,
Access Analyzer review, key deletion controls, backup retention and on-call integration. These are
environment controls and cannot be truthfully proven by this repository alone.
