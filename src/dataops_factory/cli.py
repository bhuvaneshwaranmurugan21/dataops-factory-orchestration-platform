from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from dataops_factory.compiler import compile_registry, load_registry, write_plan
from dataops_factory.simulator import run_failure_lab, simulate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dataops-factory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile", help="compile the immutable JobSpec graph")
    compile_parser.add_argument("--registry", type=Path, required=True)
    compile_parser.add_argument("--output", type=Path, required=True)
    compile_parser.add_argument("--registry-commit", default="local-reference-v1")

    simulate_parser = subparsers.add_parser("simulate", help="run the 36-job local oracle")
    simulate_parser.add_argument("--registry", type=Path, required=True)
    simulate_parser.add_argument("--work-dir", type=Path, required=True)

    failure_parser = subparsers.add_parser("failure-lab", help="execute adversarial trials")
    failure_parser.add_argument("--registry", type=Path, required=True)
    failure_parser.add_argument("--work-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "compile":
        plan = compile_registry(load_registry(args.registry), args.registry_commit)
        write_plan(plan, args.output)
        result = {"jobs": len(plan.jobs), "levels": len(plan.levels), "digest": plan.plan_digest}
    elif args.command == "simulate":
        result = simulate(args.registry, args.work_dir)
    else:
        result = run_failure_lab(args.registry, args.work_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
