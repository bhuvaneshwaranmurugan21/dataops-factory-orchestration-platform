# ADR 005: Use Redshift as publication authority

Status: accepted

Consumers need one transactional active pointer. Redshift owns immutable publication history and the
active run; a locked stored procedure performs optimistic compare-and-swap activation. DynamoDB owns
execution acceptance, not consumer visibility. The systems are reconciled explicitly rather than
described as an impossible cross-service transaction.
