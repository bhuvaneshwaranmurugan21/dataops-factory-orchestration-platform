from __future__ import annotations

from typing import Any

import pytest

from dataops_factory.aws.redshift_publication import RedshiftDataPublisher
from dataops_factory.models import ContractError


class FakeRedshiftData:
    def __init__(self, statuses: list[str] | None = None) -> None:
        self.statuses = list(statuses or [])
        self.calls: list[dict[str, Any]] = []
        self.sequence = 0

    def execute_statement(self, **kwargs: Any) -> dict[str, Any]:
        self.sequence += 1
        self.calls.append(kwargs)
        return {"Id": f"statement-{self.sequence}"}

    def describe_statement(self, **kwargs: Any) -> dict[str, Any]:
        status = self.statuses.pop(0) if self.statuses else "FINISHED"
        return {"Id": kwargs["Id"], "Status": status, "Error": "conflict"}

    def get_statement_result(self, **kwargs: Any) -> dict[str, Any]:
        if kwargs["Id"] == "statement-1":
            return {"Records": [[{"longValue": 4}]]}
        return {
            "Records": [
                [
                    {"stringValue": "run-1"},
                    {"stringValue": "sha256:" + "a" * 64},
                    {"longValue": 5},
                ]
            ]
        }


def test_publication_uses_stored_procedure_and_verifies_active_pointer() -> None:
    client = FakeRedshiftData()
    publication = RedshiftDataPublisher(
        client,
        "workgroup",
        "dataops",
        secret_arn="arn:aws:secretsmanager:ap-south-1:111122223333:secret:redshift",
        sleep=lambda _: None,
    ).activate("factory", "run-1", "sha256:" + "a" * 64)
    assert publication.version == 5
    assert client.calls[0]["SecretArn"].startswith("arn:aws:secretsmanager")
    assert client.calls[1]["Sql"].startswith("CALL control.activate_publication")
    assert {item["name"]: item["value"] for item in client.calls[1]["Parameters"]}[
        "expected_version"
    ] == "4"


def test_publication_fails_on_redshift_error_timeout_or_wrong_pointer() -> None:
    failed = FakeRedshiftData(["FAILED"])
    with pytest.raises(ContractError, match="FAILED"):
        RedshiftDataPublisher(failed, "wg", "db", sleep=lambda _: None)._current_version("factory")

    running = FakeRedshiftData(["STARTED", "STARTED"])
    with pytest.raises(TimeoutError, match="bounded"):
        RedshiftDataPublisher(
            running, "wg", "db", max_polls=2, sleep=lambda _: None
        )._current_version("factory")

    mismatch = FakeRedshiftData()
    original = mismatch.get_statement_result

    def wrong_result(**kwargs: Any) -> dict[str, Any]:
        result = original(**kwargs)
        if kwargs["Id"] == "statement-3":
            result["Records"][0][0]["stringValue"] = "another-run"
        return result

    mismatch.get_statement_result = wrong_result  # type: ignore[method-assign]
    with pytest.raises(ContractError, match="different run"):
        RedshiftDataPublisher(mismatch, "wg", "db", sleep=lambda _: None).activate(
            "factory", "run-1", "sha256:" + "a" * 64
        )
