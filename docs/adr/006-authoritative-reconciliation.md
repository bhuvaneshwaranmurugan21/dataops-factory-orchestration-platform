# ADR 006: Reconcile from authoritative service APIs

Status: accepted

Event delivery can be late, duplicated or absent. EventBridge only schedules bounded reconciliation;
Glue and EMR Serverless APIs decide terminal state. The callback record persists before dispatch,
keeps the external ID and is removed from active recovery only after the Step Functions callback is
sent.
