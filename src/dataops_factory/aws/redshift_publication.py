from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from dataops_factory.models import ContractError


class RedshiftDataClient(Protocol):
    def execute_statement(self, **kwargs: Any) -> dict[str, Any]: ...

    def describe_statement(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_statement_result(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class WarehousePublication:
    pipeline_id: str
    run_id: str
    plan_digest: str
    version: int


class RedshiftDataPublisher:
    """Small, bounded Redshift Data API adapter; SQL owns the transaction."""

    def __init__(
        self,
        client: RedshiftDataClient,
        workgroup_name: str,
        database: str,
        *,
        secret_arn: str | None = None,
        max_polls: int = 120,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_polls < 1:
            raise ValueError("max_polls must be positive")
        self.client = client
        self.workgroup_name = workgroup_name
        self.database = database
        self.secret_arn = secret_arn
        self.max_polls = max_polls
        self.sleep = sleep

    def _execute(self, sql: str, parameters: list[dict[str, str]] | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {
            "WorkgroupName": self.workgroup_name,
            "Database": self.database,
            "Sql": sql,
        }
        if self.secret_arn is not None:
            request["SecretArn"] = self.secret_arn
        if parameters:
            request["Parameters"] = parameters
        statement_id = str(self.client.execute_statement(**request)["Id"])
        for poll in range(self.max_polls):
            description = self.client.describe_statement(Id=statement_id)
            status = str(description["Status"])
            if status == "FINISHED":
                return description
            if status in {"FAILED", "ABORTED"}:
                detail = description.get("Error", "unknown Redshift Data API failure")
                raise ContractError(f"Redshift publication statement {status}: {detail}")
            if poll + 1 < self.max_polls:
                self.sleep(0.5)
        raise TimeoutError("Redshift publication statement exceeded the bounded polling window")

    def _current_version(self, pipeline_id: str) -> int:
        description = self._execute(
            "SELECT COALESCE(MAX(version), 0) AS version "
            "FROM control.active_publication WHERE pipeline_id = :pipeline_id",
            [{"name": "pipeline_id", "value": pipeline_id}],
        )
        result = self.client.get_statement_result(Id=str(description["Id"]))
        records = result.get("Records", [])
        if len(records) != 1 or len(records[0]) != 1:
            raise ContractError("Redshift returned an invalid publication-version result")
        return int(records[0][0].get("longValue", 0))

    def activate(self, pipeline_id: str, run_id: str, plan_digest: str) -> WarehousePublication:
        expected_version = self._current_version(pipeline_id)
        self._execute(
            "CALL control.activate_publication(:pipeline_id, :run_id, "
            ":plan_digest, :expected_version)",
            [
                {"name": "pipeline_id", "value": pipeline_id},
                {"name": "run_id", "value": run_id},
                {"name": "plan_digest", "value": plan_digest},
                {"name": "expected_version", "value": str(expected_version)},
            ],
        )
        publication = self.active(pipeline_id)
        if publication.run_id != run_id or publication.plan_digest != plan_digest:
            raise ContractError("Redshift activated a different run or immutable plan")
        return publication

    def active(self, pipeline_id: str) -> WarehousePublication:
        description = self._execute(
            "SELECT run_id, plan_digest, version FROM control.active_publication "
            "WHERE pipeline_id = :pipeline_id",
            [{"name": "pipeline_id", "value": pipeline_id}],
        )
        result = self.client.get_statement_result(Id=str(description["Id"]))
        records = result.get("Records", [])
        if len(records) != 1 or len(records[0]) != 3:
            raise ContractError("Redshift did not expose exactly one active publication")
        return WarehousePublication(
            pipeline_id,
            str(records[0][0]["stringValue"]),
            str(records[0][1]["stringValue"]),
            int(records[0][2]["longValue"]),
        )
