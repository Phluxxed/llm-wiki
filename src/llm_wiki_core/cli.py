from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from .compiler import compile_context
from .contracts import CompileRequest, ContractError
from .doctor import inspect_runtime
from .migration import (
    MigrationError,
    apply_migration,
    dry_run_migration,
    inspect_migration,
    rollback_migration,
    verify_migration,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-wiki")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_parser = commands.add_parser(
        "compile-context",
        help="Compile question-shaped evidence from a wiki",
    )
    compile_parser.add_argument("--wiki", required=True, help="Path to the wiki root")
    compile_parser.add_argument("--alias", required=True, help="Alias recorded in the response")
    compile_parser.add_argument("--question", required=True)
    compile_parser.add_argument("--seed", action="append", default=[], dest="seeds")
    compile_parser.add_argument("--state-view", default="current")
    compile_parser.add_argument("--target-bytes", type=int, default=48_000)
    compile_parser.add_argument("--max-bytes", type=int, default=192_000)
    compile_parser.add_argument("--target-items", type=int, default=24)
    compile_parser.add_argument("--max-items", type=int, default=96)
    compile_parser.add_argument("--max-estimated-tokens", type=int)
    compile_parser.add_argument("--contract-version", default="1")
    compile_parser.add_argument("--temporal-view", choices=("current", "historical", "transition", "lineage", "conflict"))
    compile_parser.add_argument("--request-time")
    compile_parser.add_argument("--world-at")
    compile_parser.add_argument("--known-at")
    compile_parser.add_argument("--transition-from")
    compile_parser.add_argument("--transition-to")

    doctor_parser = commands.add_parser("doctor", help="Inspect wiki runtime compatibility")
    doctor_parser.add_argument("--wiki", required=True, help="Path to the wiki root")

    migrate_parser = commands.add_parser("migrate", help="Inspect and operate an explicit wiki migration")
    migration_commands = migrate_parser.add_subparsers(dest="migration_command", required=True)
    for name in ("inspect", "dry-run", "verify"):
        command = migration_commands.add_parser(name)
        command.add_argument("--wiki", required=True, help="Path to the wiki root")
    apply_parser = migration_commands.add_parser("apply")
    apply_parser.add_argument("--wiki", required=True, help="Path to the wiki root")
    apply_parser.add_argument("--plan-hash", required=True, help="Exact hash returned by inspect/dry-run")
    rollback_parser = migration_commands.add_parser("rollback")
    rollback_parser.add_argument("--wiki", required=True, help="Path to the wiki root")
    rollback_parser.add_argument("--receipt-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "compile-context":
            temporal = None
            if args.temporal_view is not None:
                temporal = {
                    "view": args.temporal_view,
                    "request_time": args.request_time,
                }
                if args.world_at is not None:
                    temporal["world_at"] = args.world_at
                if args.known_at is not None:
                    temporal["known_at"] = args.known_at
                if args.transition_from is not None or args.transition_to is not None:
                    temporal["transition"] = {
                        "from": args.transition_from,
                        "to": args.transition_to,
                    }
            request_data = {
                "contract_version": args.contract_version,
                "alias": args.alias,
                "question": args.question,
                "seeds": args.seeds,
                "state_view": args.state_view,
                "budget": {
                    "target_bytes": args.target_bytes,
                    "max_bytes": args.max_bytes,
                    "target_items": args.target_items,
                    "max_items": args.max_items,
                    "max_estimated_tokens": args.max_estimated_tokens,
                },
            }
            if temporal is not None:
                request_data["temporal"] = temporal
            request = CompileRequest.from_mapping(
                request_data
            )
            payload = compile_context(Path(args.wiki), request).to_dict()
            _print(payload)
            return 0
        if args.command == "doctor":
            _print(inspect_runtime(Path(args.wiki)))
            return 0
        if args.command == "migrate":
            if args.migration_command == "inspect":
                payload = inspect_migration(Path(args.wiki)).to_dict()
            elif args.migration_command == "dry-run":
                payload = dry_run_migration(Path(args.wiki)).to_dict()
            elif args.migration_command == "apply":
                payload = apply_migration(Path(args.wiki), plan_hash=args.plan_hash)
            elif args.migration_command == "verify":
                payload = verify_migration(Path(args.wiki))
            elif args.migration_command == "rollback":
                payload = rollback_migration(Path(args.wiki), receipt_id=args.receipt_id)
            else:  # pragma: no cover - argparse guarantees the command set
                raise AssertionError(f"Unhandled migration command: {args.migration_command}")
            _print(payload)
            return 0 if payload.get("status") != "failed" else 3
    except (ContractError, MigrationError) as exc:
        print(json.dumps({"error": exc.to_dict()}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        error = {
            "code": "UNEXPECTED_ERROR",
            "message": "Unexpected llm-wiki CLI error",
            "details": {"type": type(exc).__name__},
        }
        print(json.dumps({"error": error}), file=sys.stderr)
        return 1
    raise AssertionError(f"Unhandled command: {args.command}")


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
