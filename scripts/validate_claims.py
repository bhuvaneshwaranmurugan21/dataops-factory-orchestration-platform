from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]


def _select(value: Any, selector: str) -> Any:
    selected = value
    for component in selector.split("."):
        if not isinstance(selected, dict) or component not in selected:
            raise AssertionError(f"claim selector does not exist: {selector}")
        selected = selected[component]
    return selected


def main() -> None:
    registry = json.loads((REPOSITORY / "evidence" / "claim-registry.json").read_text())
    claims = registry["claims"]
    if not any(claim["classification"] == "UNVERIFIED_STAGE_REQUIRED" for claim in claims):
        raise AssertionError("the claim registry must preserve the cloud-verification boundary")
    for claim in claims:
        if claim["classification"] == "UNVERIFIED_STAGE_REQUIRED":
            if "source" in claim or "expected" in claim:
                raise AssertionError(
                    "an unverified claim cannot cite local proof as cloud evidence"
                )
            continue
        source = REPOSITORY / claim["source"]
        value = _select(json.loads(source.read_text(encoding="utf-8")), claim["selector"])
        actual = len(value) if claim.get("measure") == "length" else value
        if actual != claim["expected"]:
            raise AssertionError(
                f"claim {claim['claim_id']} expected {claim['expected']!r}, found {actual!r}"
            )
    print(f"validated {len(claims)} classified claims")


if __name__ == "__main__":
    main()
