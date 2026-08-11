# ADR 003: Combine leases with fencing tokens

Status: accepted

Lease expiry alone cannot stop an old scheduler that resumes after a pause. Every successful lease
acquisition increments a fencing token; run, attempt and manifest mutations condition on that token.
Stale owners therefore lose even when their local clock or process state says they still own work.
