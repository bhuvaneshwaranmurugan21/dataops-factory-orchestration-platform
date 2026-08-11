# ADR 001: Split scheduling and workload execution

Status: accepted

Airflow owns calendar and dependency semantics; Step Functions Standard owns one bounded workload
callback. A single giant state machine would duplicate the DAG and accumulate long histories;
Airflow-only polling would consume workers and scatter recovery logic. The split keeps one graph
authority while giving every workload a managed timeout, callback and failure boundary.
