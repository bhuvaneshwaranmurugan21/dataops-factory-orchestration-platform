# ADR 002: Compile immutable execution plans

Status: accepted

Schedulers execute a checked-in compiled plan, not live mutable metadata. Strict compilation rejects
schema drift, unknown dependencies and cycles, then canonicalizes the registry, commit and levels
into one digest. This sacrifices instant metadata edits in exchange for reproducibility, reviewable
topology and defensible retry identity.
