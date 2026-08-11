from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any


def bootstrap(client: Any, workgroup: str, database: str, secret_arn: str, sql_path: Path) -> str:
    statements = [
        value.strip()
        for value in sql_path.read_text(encoding="utf-8").split("-- statement-break")
        if value.strip()
    ]
    statement_id = str(
        client.batch_execute_statement(
            WorkgroupName=workgroup,
            Database=database,
            SecretArn=secret_arn,
            Sqls=statements,
            StatementName="dataops-factory-bootstrap",
        )["Id"]
    )
    for _ in range(180):
        response = client.describe_statement(Id=statement_id)
        status = str(response["Status"])
        if status == "FINISHED":
            return statement_id
        if status in {"FAILED", "ABORTED"}:
            raise RuntimeError(f"warehouse bootstrap {status}: {response.get('Error')}")
        time.sleep(1)
    raise TimeoutError("warehouse bootstrap exceeded three minutes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap the publication catalog")
    parser.add_argument("--workgroup", required=True)
    parser.add_argument("--database", default="dataops")
    parser.add_argument("--secret-arn", required=True)
    parser.add_argument("--sql", type=Path, default=Path("warehouse/bootstrap.sql"))
    args = parser.parse_args()
    import boto3

    statement_id = bootstrap(
        boto3.client("redshift-data"),
        args.workgroup,
        args.database,
        args.secret_arn,
        args.sql,
    )
    print(statement_id)


if __name__ == "__main__":
    main()
