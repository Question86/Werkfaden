from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .corpus import build_corpus_manifest
from .auxiliary import AuxiliaryDocumentTransaction
from .engine import WorkshopEngine
from .guardian import InvestigationGuardian, guardian_required
from .migration import AuthorityMigration
from .repair import BootstrapRepair
from .universal_migration import UniversalSourceSetMigration
from .util import WorkshopError


DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "workshop.config.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runtime-sync-workshop",
        description="Fail-closed Runtime/blueprint/KAIROS synchronization workshop",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="workshop config JSON")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="read and verify all live layers; persist a machine-local receipt")
    sub.add_parser("manifest", help="emit the complete read-only corpus manifest")
    sub.add_parser("seal", help="create a trusted content-addressed mirror after full verification")
    sub.add_parser(
        "bootstrap-stage",
        help="stage the verified document-only legacy repair in an isolated KAIROS shadow",
    )

    guard_start = sub.add_parser(
        "guard-start",
        help="start one deterministic Human-problem-to-patch investigation ledger",
    )
    guard_start.add_argument("--problem", required=True)
    guard_start.add_argument("--memory-file", required=True)
    guard_start.add_argument(
        "--memory-ref",
        action="append",
        required=True,
        help="relevant canonical Memory passage/code consulted for HUMAN PROBLEM; repeatable",
    )

    guard_step = sub.add_parser(
        "guard-step",
        help="advance exactly one guardian state; out-of-order transitions fail closed",
    )
    guard_step.add_argument("guardian_id")
    guard_step.add_argument("--step", required=True)
    guard_step.add_argument("--summary", required=True)
    guard_step.add_argument(
        "--memory-ref",
        action="append",
        required=True,
        help="relevant canonical Memory passage/code re-verified for this state; repeatable",
    )
    guard_step.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="evidence/receipt/graph handle supporting this state; repeatable",
    )
    guard_step.add_argument(
        "--source",
        action="append",
        default=[],
        help="exact governed source frozen by PROVE; only meaningful for PROVE",
    )

    guard_status = sub.add_parser("guard-status", help="show one guardian state")
    guard_status.add_argument("guardian_id")
    guard_status.add_argument("--full", action="store_true")

    checkout = sub.add_parser("checkout", help="open one exclusive transaction over named governed sources")
    checkout.add_argument("--source", action="append", required=True, help="governed source path; repeatable")
    checkout.add_argument("--purpose", required=True)
    checkout.add_argument("--test", action="append", default=[], help="configured dependent test whose Runtime owner is selected")
    checkout.add_argument(
        "--guardian",
        default="",
        help="guardian session at PROVE; required when guardian_required=true",
    )
    checkout.add_argument(
        "--guardian-memory-ref",
        action="append",
        default=[],
        help="canonical Memory passage/code re-verified immediately before PATCH checkout",
    )

    source_set_checkout = sub.add_parser(
        "source-set-checkout",
        help="open a Workshop-owned candidate tree for static source create/delete/rename",
    )
    source_set_checkout.add_argument("--purpose", required=True)

    source_set_recover = sub.add_parser(
        "source-set-recover-orphan",
        help="release a proven pre-apply orphan lease from an interrupted source-set checkout",
    )
    source_set_recover.add_argument("transaction_id")
    source_set_recover.add_argument("--expected-sealed-package-sha256", required=True)
    for name, help_text in (
        ("source-set-prepare", "derive source/Markdown/membership deltas from the Workshop-owned candidate tree"),
        ("source-set-verify", "verify source-set topology and KAIROS projection in an isolated shadow"),
        ("source-set-apply", "atomically apply the verified source-set migration and reseal"),
        ("source-set-abort", "abort the source-set candidate while live bytes remain sealed"),
        ("source-set-transaction", "show one universal source-set transaction"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("transaction_id")

    auxiliary_checkout = sub.add_parser(
        "auxiliary-checkout",
        help="open one exclusive document-only transaction over configured process authorities",
    )
    auxiliary_checkout.add_argument("--document", action="append", required=True)
    auxiliary_checkout.add_argument("--purpose", required=True)

    for name, help_text in (
        ("scientific-baseline", "build and run the sealed pre-edit productive scientific reference"),
        ("scientific-reproducibility", "build the unchanged sealed source twice at one canonical slot"),
        ("prepare", "regenerate mechanical blueprint layers and enforce metadata review"),
        ("verify", "promote prepared documents into an isolated KAIROS database snapshot"),
        ("scientific-verify", "build the candidate and require identical-input H0 statistic bytes"),
        ("apply", "write named live files, heartbeat KAIROS, and require a bit-exact postcheck"),
        ("abort", "abort a pre-apply transaction only if the live corpus is unchanged"),
        ("recover", "release a recovery lease only after a complete live postcheck"),
        ("transaction", "show one transaction state and journal"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("transaction_id")

    stale_abort = sub.add_parser(
        "stale-abort",
        help="release a pre-apply stale checkout only after exact current-package and live-scope verification",
    )
    stale_abort.add_argument("transaction_id")
    stale_abort.add_argument("--expected-current-package-sha256", required=True)

    authority_recover = sub.add_parser(
        "authority-recover",
        help="terminalize one explicitly authorized lease-less pre-apply checkout without adopting live bytes",
    )
    authority_recover.add_argument("transaction_id")
    authority_recover.add_argument("--decision-id", required=True)
    authority_recover.add_argument("--decision-sha256", required=True)
    authority_recover.add_argument("--exception-report-id", required=True)
    authority_recover.add_argument("--exception-report-sha256", required=True)
    authority_recover.add_argument("--blocker-report-id", required=True)
    authority_recover.add_argument("--blocker-report-sha256", required=True)
    authority_recover.add_argument("--expected-current-package-sha256", required=True)
    authority_recover.add_argument("--expected-sealed-package-sha256", required=True)

    stage_authority = sub.add_parser(
        "scientific-stage-runner-authority",
        help="stage one verified reproducible runner as a content-addressed scientific authority",
    )
    stage_authority.add_argument("transaction_id")
    stage_authority.add_argument("--decision-id", required=True)
    stage_authority.add_argument("--expected-runner-sha256", required=True)

    for name, help_text in (
        ("auxiliary-verify", "verify staged process-document bytes without touching Runtime"),
        ("auxiliary-apply", "apply verified process documents and reseal the full corpus"),
        ("auxiliary-abort", "abort a process-document transaction while live bytes are unchanged"),
        ("auxiliary-transaction", "show one process-document transaction"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("transaction_id")

    for name, help_text in (
        ("bootstrap-apply", "apply a shadow-verified document-only bootstrap repair"),
        ("bootstrap-abort", "abort a staged bootstrap repair while live bytes are unchanged"),
        ("bootstrap-recover", "close a bootstrap recovery lease after exact live postcheck"),
        ("bootstrap-transaction", "show one bootstrap repair transaction"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("transaction_id")

    authority_checkout = sub.add_parser(
        "authority-checkout",
        help="stage an explicit compiler-backed authority migration from an isolated candidate tree",
    )
    authority_checkout.add_argument("--candidate-root", required=True)
    authority_checkout.add_argument("--compile-commands", required=True)
    authority_checkout.add_argument("--purpose", required=True)
    for name, help_text in (
        ("authority-prepare", "prepare reviewed candidate code/document/build authority"),
        ("authority-verify", "verify the candidate authority in an isolated KAIROS/Workshop shadow"),
        ("authority-apply", "atomically apply the verified authority migration and reseal"),
        ("authority-abort", "abort a pre-apply authority migration while live bytes remain sealed"),
        ("authority-transaction", "show one authority migration state and journal"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("transaction_id")

    sub.add_parser("acl-plan", help="show the OS boundary required to make workshop-only writes enforceable")
    return parser


def _acl_plan(engine: WorkshopEngine) -> dict[str, Any]:
    config = engine.config
    return {
        "schema": "runtime-sync-acl-plan/v1",
        "applied": False,
        "reason": "ACL changes are deliberately not automatic; apply them only after naming a dedicated service identity and recovery principal.",
        "required_boundary": {
            "read_only_for_normal_agents": [
                str(config.runtime_root),
                str(config.blueprint_root),
                str(config.managed_blueprint_root),
            ],
            "write_allowed_for_workshop_service_only": [
                str(config.runtime_root),
                str(config.blueprint_root),
                str(config.managed_blueprint_root),
                str(config.state_directory),
                str(config.transaction_directory),
            ],
            "kairos_database": "Never grant direct SQL write authority to the workshop; KAIROS heartbeat remains the sole projection writer.",
            "recovery": "Retain one separately authenticated administrator/recovery principal.",
        },
        "logical_guard_active_now": "Every checkout binds a full package seal; drift blocks prepare, verify, apply, abort, and recovery.",
    }


def _guardian(engine: WorkshopEngine) -> InvestigationGuardian:
    return InvestigationGuardian(
        state_directory=engine.config.state_directory,
        kairos_database=engine.config.kairos_database,
    )


def _guardian_package(engine: WorkshopEngine) -> str:
    status = engine.status(persist=False)
    if not status.get("verified"):
        raise WorkshopError(
            "GUARDIAN_BASELINE_UNVERIFIED",
            "guardian requires a fully verified Workshop corpus before investigation",
            details=status.get("issues"),
        )
    seal = status.get("seal")
    if not isinstance(seal, dict) or not seal.get("matches_current"):
        raise WorkshopError(
            "GUARDIAN_BASELINE_UNSEALED",
            "guardian requires the current verified corpus to match the trusted Workshop seal",
        )
    return str(status["package_sha256"])


def _guarded_mutation_path_block(engine: WorkshopEngine, command: str) -> None:
    if guardian_required(engine.config.raw):
        raise WorkshopError(
            "GUARDIAN_MUTATION_PATH_UNSUPPORTED",
            f"{command} is blocked while guardian_required=true; guardian v1 authorizes "
            "exact governed-source checkout only",
        )


def execute(arguments: argparse.Namespace) -> dict[str, Any]:
    engine = WorkshopEngine(Path(arguments.config))
    bootstrap = BootstrapRepair(engine)
    auxiliary = AuxiliaryDocumentTransaction(engine)
    authority = AuthorityMigration(engine)
    source_set = UniversalSourceSetMigration(engine)
    guard = _guardian(engine)
    command = arguments.command
    if command == "status":
        return engine.status()
    if command == "manifest":
        return build_corpus_manifest(engine.config)
    if command == "seal":
        return engine.seal()
    if command == "guard-start":
        return guard.start(
            problem=arguments.problem,
            memory_file=Path(arguments.memory_file),
            memory_refs=arguments.memory_ref,
            package_sha256=_guardian_package(engine),
        )
    if command == "guard-step":
        return guard.advance(
            arguments.guardian_id,
            step=arguments.step,
            summary=arguments.summary,
            memory_refs=arguments.memory_ref,
            evidence_refs=arguments.evidence,
            package_sha256=_guardian_package(engine),
            sources=arguments.source,
        )
    if command == "guard-status":
        return guard.status(arguments.guardian_id, full=arguments.full)
    if command == "bootstrap-stage":
        return bootstrap.stage()
    if command == "checkout":
        required = guardian_required(engine.config.raw)
        guardian_id = str(arguments.guardian or "").strip()
        if required and not guardian_id:
            raise WorkshopError(
                "GUARDIAN_REQUIRED",
                "coding is blocked: supply --guardian after completing the investigation through PROVE",
            )
        package_sha256 = ""
        if guardian_id:
            if not arguments.guardian_memory_ref:
                raise WorkshopError(
                    "GUARDIAN_PATCH_MEMORY_REQUIRED",
                    "PATCH checkout requires at least one --guardian-memory-ref re-verified immediately before coding",
                )
            package_sha256 = _guardian_package(engine)
            guard.assert_checkout(
                guardian_id,
                sources=arguments.source,
                package_sha256=package_sha256,
            )
        result = engine.checkout(arguments.source, purpose=arguments.purpose, tests=arguments.test)
        if guardian_id:
            try:
                guardian_state = guard.bind_checkout(
                    guardian_id,
                    transaction_id=result["transaction_id"],
                    package_sha256=package_sha256,
                    memory_refs=arguments.guardian_memory_ref,
                    purpose=arguments.purpose,
                )
            except Exception:
                try:
                    engine.abort(result["transaction_id"])
                except Exception:
                    pass
                raise
            result["guardian"] = guardian_state
        return result
    if command == "source-set-checkout":
        _guarded_mutation_path_block(engine, command)
        return source_set.checkout(purpose=arguments.purpose)
    if command == "source-set-recover-orphan":
        return source_set.recover_orphan_checkout(
            arguments.transaction_id,
            expected_sealed_package_sha256=arguments.expected_sealed_package_sha256,
        )
    if command == "source-set-prepare":
        return source_set.prepare(arguments.transaction_id)
    if command == "source-set-verify":
        return source_set.verify(arguments.transaction_id)
    if command == "source-set-apply":
        return source_set.apply(arguments.transaction_id)
    if command == "source-set-abort":
        return source_set.abort(arguments.transaction_id)
    if command == "source-set-transaction":
        return source_set.transaction_status(arguments.transaction_id)
    if command == "auxiliary-checkout":
        return auxiliary.checkout(arguments.document, purpose=arguments.purpose)
    if command == "prepare":
        return engine.prepare(arguments.transaction_id)
    if command == "scientific-baseline":
        return engine.scientific_baseline(arguments.transaction_id)
    if command == "scientific-reproducibility":
        return engine.scientific_reproducibility(arguments.transaction_id)
    if command == "scientific-stage-runner-authority":
        return engine.scientific_stage_runner_authority(
            arguments.transaction_id,
            decision_id=arguments.decision_id,
            expected_runner_sha256=arguments.expected_runner_sha256,
        )
    if command == "verify":
        return engine.verify(arguments.transaction_id)
    if command == "scientific-verify":
        return engine.scientific_verify(arguments.transaction_id)
    if command == "apply":
        return engine.apply(arguments.transaction_id)
    if command == "abort":
        return engine.abort(arguments.transaction_id)
    if command == "stale-abort":
        return engine.stale_abort(
            arguments.transaction_id,
            expected_current_package_sha256=arguments.expected_current_package_sha256,
        )
    if command == "authority-recover":
        return engine.authority_recover(
            arguments.transaction_id,
            decision_id=arguments.decision_id,
            decision_sha256=arguments.decision_sha256,
            exception_report_id=arguments.exception_report_id,
            exception_report_sha256=arguments.exception_report_sha256,
            blocker_report_id=arguments.blocker_report_id,
            blocker_report_sha256=arguments.blocker_report_sha256,
            expected_current_package_sha256=arguments.expected_current_package_sha256,
            expected_sealed_package_sha256=arguments.expected_sealed_package_sha256,
        )
    if command == "recover":
        return engine.recover(arguments.transaction_id)
    if command == "transaction":
        return engine.transaction_status(arguments.transaction_id)
    if command == "bootstrap-apply":
        return bootstrap.apply(arguments.transaction_id)
    if command == "bootstrap-abort":
        return bootstrap.abort(arguments.transaction_id)
    if command == "bootstrap-recover":
        return bootstrap.recover(arguments.transaction_id)
    if command == "bootstrap-transaction":
        return bootstrap.transaction_status(arguments.transaction_id)
    if command == "auxiliary-verify":
        return auxiliary.verify(arguments.transaction_id)
    if command == "auxiliary-apply":
        return auxiliary.apply(arguments.transaction_id)
    if command == "auxiliary-abort":
        return auxiliary.abort(arguments.transaction_id)
    if command == "auxiliary-transaction":
        return auxiliary.transaction_status(arguments.transaction_id)
    if command == "authority-checkout":
        _guarded_mutation_path_block(engine, command)
        return authority.checkout(
            candidate_root=Path(arguments.candidate_root),
            compile_commands=Path(arguments.compile_commands),
            purpose=arguments.purpose,
        )
    if command == "authority-prepare":
        return authority.prepare(arguments.transaction_id)
    if command == "authority-verify":
        return authority.verify(arguments.transaction_id)
    if command == "authority-apply":
        return authority.apply(arguments.transaction_id)
    if command == "authority-abort":
        return authority.abort(arguments.transaction_id)
    if command == "authority-transaction":
        return authority.transaction_status(arguments.transaction_id)
    if command == "acl-plan":
        return _acl_plan(engine)
    raise WorkshopError("COMMAND_UNKNOWN", f"unsupported command: {command}")


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
