from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._cli_core_v1 import load_cli_core
from .auxiliary import AuxiliaryDocumentTransaction
from .engine import WorkshopEngine
from .guardian import guardian_required
from .migration import AuthorityMigration
from .universal_migration import UniversalSourceSetMigration
from .util import WorkshopError

_legacy = load_cli_core()
DEFAULT_CONFIG = _legacy.DEFAULT_CONFIG


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise RuntimeError("legacy Workshop parser has no subparsers")


def _add_guard_args(parser: argparse.ArgumentParser) -> None:
    if not any(action.dest == "guardian" for action in parser._actions):
        parser.add_argument("--guardian", default="", help="active GRD2_ patch-session guardian")
    if not any(action.dest == "guardian_state_ticket" for action in parser._actions):
        parser.add_argument(
            "--guardian-state-ticket",
            default="",
            help="PATCH state ticket returned by guard-enter before checkout",
        )
    if not any(action.dest == "guardian_memory_ref" for action in parser._actions):
        parser.add_argument("--guardian-memory-ref", action="append", default=[], help=argparse.SUPPRESS)


def _parser() -> argparse.ArgumentParser:
    parser = _legacy._parser()
    sub = _subparsers(parser)

    step = sub.choices.get("guard-step")
    if step is not None:
        if not any(action.dest == "scope_file" for action in step._actions):
            step.add_argument(
                "--scope-file",
                default="",
                help="werkfaden-patch-scope/v1 JSON used by PROVE; omit for normal source-only scope",
            )
        if not any(action.dest == "state_ticket" for action in step._actions):
            step.add_argument("--state-ticket", required=True, help="state ticket returned by guard-enter")
        for action in step._actions:
            if action.dest == "memory_ref":
                action.required = False

    checkout = sub.choices.get("checkout")
    if checkout is not None:
        if not any(action.dest == "guardian_state_ticket" for action in checkout._actions):
            checkout.add_argument("--guardian-state-ticket", default="", help="PATCH state ticket returned by guard-enter")
        for action in checkout._actions:
            if action.dest == "guardian_memory_ref":
                action.required = False

    if "guard-enter" not in sub.choices:
        command = sub.add_parser("guard-enter", help="read state-relevant canonical Memory and issue a one-state execution ticket")
        command.add_argument("guardian_id")
        command.add_argument("--step", required=True, choices=("HYPOTHESIS", "FALSIFIER_1", "FALSIFIER_2", "MAP", "COUNTERPROBE", "EXACT_SOURCE", "PROVE", "PATCH", "HEARTBEAT", "FRESH_RUN"))
        command.add_argument("--memory-ref", action="append", default=[], help="optional extra canonical Memory selector; configured state requirements are always included")

    for name in ("source-set-checkout", "auxiliary-checkout", "authority-checkout"):
        target = sub.choices.get(name)
        if target is not None:
            _add_guard_args(target)

    if "guard-reframe" not in sub.choices:
        command = sub.add_parser("guard-reframe", help="return an idle Guardian session to HYPOTHESIS or MAP without rewriting history")
        command.add_argument("guardian_id")
        command.add_argument("--target", required=True, choices=("HYPOTHESIS", "MAP"))
        command.add_argument("--reason", required=True)
        command.add_argument("--memory-ref", action="append", required=True)

    if "guard-patch-close" not in sub.choices:
        command = sub.add_parser("guard-patch-close", help="close a fully consumed multi-transaction patch session and bind its final verified heartbeat")
        command.add_argument("guardian_id")
        command.add_argument("--state-ticket", required=True)

    if "guard-fresh-run" not in sub.choices:
        command = sub.add_parser("guard-fresh-run", help="close the Guardian only from a content-bound fresh-run receipt")
        command.add_argument("guardian_id")
        command.add_argument("--receipt-file", required=True)
        command.add_argument("--state-ticket", required=True)

    if "guard-reconcile" not in sub.choices:
        command = sub.add_parser("guard-reconcile", help="reconcile a provable Workshop/Guardian crash window without adopting unverified bytes")
        command.add_argument("guardian_id")

    return parser


def _scope_file(path: str) -> dict | None:
    value = str(path or "").strip()
    if not value:
        return None
    payload = json.loads(Path(value).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise WorkshopError("GUARDIAN_SCOPE_SCHEMA_INVALID", "scope file must contain a JSON object")
    return payload


def _require_guardian_checkout(engine: WorkshopEngine, arguments: argparse.Namespace) -> tuple[str, str]:
    required = guardian_required(engine.config.raw)
    guardian_id = str(getattr(arguments, "guardian", "") or "").strip()
    ticket = str(getattr(arguments, "guardian_state_ticket", "") or "").strip()
    if required and not guardian_id:
        raise WorkshopError("GUARDIAN_REQUIRED", "this checkout requires --guardian after PROVE")
    if guardian_id and not ticket:
        raise WorkshopError("GUARDIAN_MEMORY_GATE_MISSING", "guarded checkout requires --guardian-state-ticket from guard-enter PATCH")
    return guardian_id, ticket


def execute(arguments: argparse.Namespace) -> dict:
    engine = WorkshopEngine(Path(arguments.config))
    guard = engine.guardian()
    command = arguments.command

    if command == "guard-start":
        return guard.start(
            problem=arguments.problem,
            memory_file=Path(arguments.memory_file),
            memory_selectors=arguments.memory_ref,
            package_sha256=engine._verified_package(require_seal=True),
        )
    if command == "guard-enter":
        return guard.enter_state(
            arguments.guardian_id,
            step=arguments.step,
            memory_selectors=arguments.memory_ref,
            package_sha256=engine._verified_package(require_seal=True),
        )
    if command == "guard-step":
        return guard.advance(
            arguments.guardian_id,
            step=arguments.step,
            summary=arguments.summary,
            state_ticket_id=arguments.state_ticket,
            evidence_refs=arguments.evidence,
            package_sha256=engine._verified_package(require_seal=True),
            sources=arguments.source,
            scope_contract=_scope_file(getattr(arguments, "scope_file", "")),
        )
    if command == "guard-status":
        return guard.status(arguments.guardian_id, full=arguments.full)
    if command == "guard-reframe":
        return guard.reframe(
            arguments.guardian_id,
            target=arguments.target,
            reason=arguments.reason,
            memory_selectors=arguments.memory_ref,
            package_sha256=engine._verified_package(require_seal=True),
        )
    if command == "guard-patch-close":
        return guard.patch_close(
            arguments.guardian_id,
            state_ticket_id=arguments.state_ticket,
            package_sha256=engine._verified_package(require_seal=True),
        )
    if command == "guard-fresh-run":
        return guard.fresh_run(
            arguments.guardian_id,
            receipt_file=Path(arguments.receipt_file),
            state_ticket_id=arguments.state_ticket,
            package_sha256=engine._verified_package(require_seal=True),
        )
    if command == "guard-reconcile":
        return engine.guardian_reconcile(arguments.guardian_id)

    if command == "checkout":
        guardian_id, ticket = _require_guardian_checkout(engine, arguments)
        return engine.checkout(
            arguments.source,
            purpose=arguments.purpose,
            tests=arguments.test,
            guardian_id=guardian_id or None,
            guardian_state_ticket=ticket or None,
        )

    if command == "auxiliary-checkout":
        guardian_id, ticket = _require_guardian_checkout(engine, arguments)
        auxiliary = AuxiliaryDocumentTransaction(engine)
        if not guardian_id:
            return auxiliary.checkout(arguments.document, purpose=arguments.purpose)
        request = {"documents": sorted(set(arguments.document))}
        with engine.guardian_transaction(
            guardian_id=guardian_id,
            kind="auxiliary",
            request=request,
            state_ticket_id=ticket,
        ):
            return auxiliary.checkout(arguments.document, purpose=arguments.purpose)

    if command == "source-set-checkout":
        guardian_id, ticket = _require_guardian_checkout(engine, arguments)
        source_set = UniversalSourceSetMigration(engine)
        if not guardian_id:
            return source_set.checkout(purpose=arguments.purpose)
        status = guard.status(guardian_id, full=True)
        request = (((status.get("patch_session") or {}).get("available") or {}).get("source_set") or {"operations": []})
        with engine.guardian_transaction(
            guardian_id=guardian_id,
            kind="source_set",
            request=request,
            state_ticket_id=ticket,
        ):
            return source_set.checkout(purpose=arguments.purpose)

    if command == "authority-checkout":
        guardian_id, ticket = _require_guardian_checkout(engine, arguments)
        authority = AuthorityMigration(engine)
        if not guardian_id:
            return authority.checkout(
                candidate_root=Path(arguments.candidate_root),
                compile_commands=Path(arguments.compile_commands),
                purpose=arguments.purpose,
            )
        status = guard.status(guardian_id, full=True)
        request = (((status.get("patch_session") or {}).get("available") or {}).get("authority") or {"add_headers": [], "remove_headers": []})
        with engine.guardian_transaction(
            guardian_id=guardian_id,
            kind="authority",
            request=request,
            state_ticket_id=ticket,
        ):
            return authority.checkout(
                candidate_root=Path(arguments.candidate_root),
                compile_commands=Path(arguments.compile_commands),
                purpose=arguments.purpose,
            )

    if command == "bootstrap-stage" and guardian_required(engine.config.raw):
        raise WorkshopError(
            "GUARDIAN_MAINTENANCE_BOUNDARY",
            "bootstrap repair is a pre-seal maintenance path and is unavailable while Guardian enforcement is active",
        )

    return _legacy.execute(arguments)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        result = execute(arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if arguments.command in {"status", "manifest"} and not result.get("verified", False):
            return 2
        return 0
    except WorkshopError as exc:
        print(json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": {"code": type(exc).__name__, "message": str(exc)}}, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
