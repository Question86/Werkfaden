from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable

from .util import WorkshopError, atomic_write_json, sha256_bytes, sha256_text, utc_now


SCHEMA = "werkfaden-guardian/v1"
MAX_MEMORY_BYTES = 8 * 1024 * 1024

PRE_PATCH_STEPS = (
    "HYPOTHESIS",
    "FALSIFIER_1",
    "FALSIFIER_2",
    "MAP",
    "COUNTERPROBE",
    "EXACT_SOURCE",
    "PROVE",
)
POST_PATCH_STEPS = ("WORKSHOP", "HEARTBEAT", "FRESH_RUN")
ALL_STEPS = (*PRE_PATCH_STEPS, "PATCH", *POST_PATCH_STEPS)
EVIDENCE_REQUIRED = {
    "FALSIFIER_1",
    "FALSIFIER_2",
    "MAP",
    "COUNTERPROBE",
    "EXACT_SOURCE",
    "PROVE",
    "WORKSHOP",
    "HEARTBEAT",
    "FRESH_RUN",
}


def guardian_required(raw_config: dict[str, Any]) -> bool:
    """A config flag is authoritative. The environment can force the guard on, never off."""
    configured = bool(raw_config.get("guardian_required", False))
    forced = os.environ.get("WERKFADEN_GUARDIAN_REQUIRED", "").strip().casefold()
    return configured or forced in {"1", "true", "yes", "on"}


def _normalize_refs(values: Iterable[str], *, label: str) -> list[str]:
    refs = sorted({str(value).strip() for value in values if str(value).strip()})
    if not refs:
        raise WorkshopError("GUARDIAN_EVIDENCE_MISSING", f"{label} requires at least one reference")
    if any(len(value) > 1024 for value in refs):
        raise WorkshopError("GUARDIAN_EVIDENCE_INVALID", f"{label} reference exceeds 1024 characters")
    return refs


def _normalize_sources(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = str(value).replace("\\", "/").strip()
        path = Path(normalized)
        if not normalized or path.is_absolute() or ".." in path.parts:
            raise WorkshopError(
                "GUARDIAN_SCOPE_INVALID",
                f"guardian source scope must be a safe project-relative path: {value!r}",
            )
        result.append(normalized)
    result = sorted(set(result))
    if not result:
        raise WorkshopError("GUARDIAN_SCOPE_EMPTY", "PROVE requires at least one frozen source")
    return result


class InvestigationGuardian:
    """Small deterministic ledger that gates Workshop checkout behind the investigation loop.

    This guardian verifies sequence, evidence presence, canonical-memory identity, exact-source
    receipt existence, baseline stability, and frozen patch scope. It deliberately does not
    judge whether an LLM's hypothesis or causal explanation is semantically correct.
    """

    def __init__(self, *, state_directory: Path, kairos_database: Path) -> None:
        self.directory = Path(state_directory) / "guardian"
        self.kairos_database = Path(kairos_database)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, guardian_id: str) -> Path:
        if not guardian_id.startswith("GRD_") or len(guardian_id) != 28:
            raise WorkshopError("GUARDIAN_ID_INVALID", f"invalid guardian id: {guardian_id}")
        path = (self.directory / f"{guardian_id}.json").resolve()
        try:
            path.relative_to(self.directory.resolve())
        except ValueError as exc:
            raise WorkshopError("GUARDIAN_PATH_ESCAPE", "guardian path escapes state directory") from exc
        return path

    def _load(self, guardian_id: str) -> dict[str, Any]:
        path = self._path(guardian_id)
        if not path.is_file():
            raise WorkshopError("GUARDIAN_MISSING", f"guardian session does not exist: {guardian_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != SCHEMA or payload.get("guardian_id") != guardian_id:
            raise WorkshopError("GUARDIAN_CORRUPT", f"invalid guardian session: {guardian_id}")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self._path(str(payload["guardian_id"])), payload)

    @staticmethod
    def _memory_facts(memory_file: Path) -> dict[str, Any]:
        path = Path(memory_file)
        if not path.is_file():
            raise WorkshopError("GUARDIAN_MEMORY_MISSING", f"canonical Memory file is missing: {path}")
        stat = path.stat()
        if stat.st_size <= 0 or stat.st_size > MAX_MEMORY_BYTES:
            raise WorkshopError(
                "GUARDIAN_MEMORY_INVALID",
                f"canonical Memory must be between 1 and {MAX_MEMORY_BYTES} bytes",
            )
        raw = path.read_bytes()
        return {
            "path": str(path.resolve()),
            "size": int(stat.st_size),
            "sha256": sha256_bytes(raw),
        }

    def _assert_memory_stable(self, payload: dict[str, Any]) -> None:
        expected = payload.get("memory") or {}
        current = self._memory_facts(Path(str(expected.get("path", ""))))
        if (
            current["sha256"] != expected.get("sha256")
            or current["size"] != expected.get("size")
        ):
            raise WorkshopError(
                "GUARDIAN_MEMORY_DRIFT",
                "canonical Memory changed after the investigation began; restart from HUMAN PROBLEM",
                details={"expected": expected, "current": current},
            )

    @staticmethod
    def _assert_package(payload: dict[str, Any], package_sha256: str) -> None:
        if payload.get("baseline_package_sha256") != package_sha256:
            raise WorkshopError(
                "GUARDIAN_BASELINE_DRIFT",
                "governed corpus changed after the investigation began; restart from HUMAN PROBLEM",
                details={
                    "expected": payload.get("baseline_package_sha256"),
                    "current": package_sha256,
                },
            )

    def _validate_source_receipt(self, inspection_id: str) -> dict[str, Any]:
        if not inspection_id.startswith("SIR_"):
            raise WorkshopError(
                "GUARDIAN_SOURCE_RECEIPT_REQUIRED",
                "EXACT_SOURCE requires a verified KAIROS source-inspection receipt (SIR_...)",
            )
        if not self.kairos_database.is_file():
            raise WorkshopError(
                "GUARDIAN_KAIROS_DB_MISSING",
                f"KAIROS database is missing: {self.kairos_database}",
            )
        resolved = self.kairos_database.resolve()
        connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                """
                SELECT inspection_id,status,receipt_json
                FROM source_inspection_receipts
                WHERE inspection_id=?
                """,
                (inspection_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise WorkshopError(
                "GUARDIAN_SOURCE_RECEIPT_UNAVAILABLE",
                "cannot verify the KAIROS source-inspection receipt",
            ) from exc
        finally:
            connection.close()
        if not row:
            raise WorkshopError(
                "GUARDIAN_SOURCE_RECEIPT_MISSING",
                f"source-inspection receipt does not exist: {inspection_id}",
            )
        receipt = json.loads(row["receipt_json"])
        if row["status"] != "VERIFIED" or receipt.get("status") != "VERIFIED":
            raise WorkshopError(
                "GUARDIAN_SOURCE_NOT_VERIFIED",
                f"source-inspection receipt is not VERIFIED: {inspection_id}",
            )
        if not receipt.get("scope_stable"):
            raise WorkshopError(
                "GUARDIAN_SOURCE_SCOPE_UNSTABLE",
                f"source-inspection scope was not stable: {inspection_id}",
            )
        return {
            "inspection_id": inspection_id,
            "paths": list(receipt.get("paths") or []),
            "match_count": int(receipt.get("match_count", 0)),
            "created_at": receipt.get("created_at"),
        }

    @staticmethod
    def _next_after(step: str) -> str | None:
        index = ALL_STEPS.index(step)
        return ALL_STEPS[index + 1] if index + 1 < len(ALL_STEPS) else None

    @staticmethod
    def _view(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "guardian_id": payload["guardian_id"],
            "current_step": payload["current_step"],
            "next_step": payload.get("next_step"),
            "closed": bool(payload.get("closed", False)),
            "baseline_package_sha256": payload["baseline_package_sha256"],
            "memory_sha256": payload["memory"]["sha256"],
            "frozen_sources": list(payload.get("frozen_sources") or []),
            "bound_transaction": payload.get("bound_transaction"),
            "step_count": len(payload.get("steps") or []),
        }

    def start(
        self,
        *,
        problem: str,
        memory_file: Path,
        memory_refs: Iterable[str],
        package_sha256: str,
    ) -> dict[str, Any]:
        problem = str(problem).strip()
        if not 12 <= len(problem) <= 8000:
            raise WorkshopError(
                "GUARDIAN_PROBLEM_INVALID",
                "Human problem must contain 12 to 8000 characters",
            )
        memory_refs_list = _normalize_refs(memory_refs, label="HUMAN_PROBLEM")
        memory = self._memory_facts(memory_file)
        created_at = utc_now()
        guardian_id = "GRD_" + sha256_text(
            f"{uuid.uuid4().hex}|{created_at}|{package_sha256}|{memory['sha256']}|{problem}"
        )[:24]
        payload = {
            "schema": SCHEMA,
            "guardian_id": guardian_id,
            "created_at": created_at,
            "updated_at": created_at,
            "closed": False,
            "baseline_package_sha256": package_sha256,
            "memory": memory,
            "current_step": "HUMAN_PROBLEM",
            "next_step": "HYPOTHESIS",
            "frozen_sources": [],
            "bound_transaction": None,
            "steps": [
                {
                    "step": "HUMAN_PROBLEM",
                    "at": created_at,
                    "summary": problem,
                    "summary_sha256": sha256_text(problem),
                    "memory_refs": memory_refs_list,
                    "evidence_refs": [],
                }
            ],
        }
        self._save(payload)
        return self._view(payload)

    def advance(
        self,
        guardian_id: str,
        *,
        step: str,
        summary: str,
        memory_refs: Iterable[str],
        evidence_refs: Iterable[str],
        package_sha256: str,
        sources: Iterable[str] = (),
    ) -> dict[str, Any]:
        payload = self._load(guardian_id)
        if payload.get("closed"):
            raise WorkshopError("GUARDIAN_CLOSED", f"guardian session is closed: {guardian_id}")
        self._assert_memory_stable(payload)

        normalized_step = str(step).strip().upper().replace("-", "_")
        if normalized_step in PRE_PATCH_STEPS:
            self._assert_package(payload, package_sha256)
        if normalized_step == "PATCH":
            raise WorkshopError(
                "GUARDIAN_PATCH_DIRECT_FORBIDDEN",
                "PATCH is entered only by a successful guarded Workshop checkout",
            )
        expected = payload.get("next_step")
        if normalized_step != expected:
            raise WorkshopError(
                "GUARDIAN_STEP_ORDER",
                f"expected {expected}, received {normalized_step}",
                details={"current_step": payload.get("current_step"), "next_step": expected},
            )
        summary = str(summary).strip()
        if len(summary) < 12:
            raise WorkshopError(
                "GUARDIAN_SUMMARY_TOO_SHORT",
                f"{normalized_step} summary must contain at least 12 characters",
            )
        memory_refs_list = _normalize_refs(memory_refs, label=normalized_step)
        evidence_refs_list = sorted(
            {str(value).strip() for value in evidence_refs if str(value).strip()}
        )
        if normalized_step in EVIDENCE_REQUIRED and not evidence_refs_list:
            raise WorkshopError(
                "GUARDIAN_EVIDENCE_MISSING",
                f"{normalized_step} requires at least one evidence reference",
            )

        step_record: dict[str, Any] = {
            "step": normalized_step,
            "at": utc_now(),
            "package_sha256": package_sha256,
            "summary": summary,
            "summary_sha256": sha256_text(summary),
            "memory_refs": memory_refs_list,
            "evidence_refs": evidence_refs_list,
        }

        if normalized_step == "WORKSHOP":
            transaction_id = payload.get("bound_transaction")
            if not transaction_id or transaction_id not in evidence_refs_list:
                raise WorkshopError(
                    "GUARDIAN_WORKSHOP_RECEIPT_REQUIRED",
                    "WORKSHOP must reference the exact transaction bound at PATCH checkout",
                )

        if normalized_step == "EXACT_SOURCE":
            sir_refs = [value for value in evidence_refs_list if value.startswith("SIR_")]
            if not sir_refs:
                raise WorkshopError(
                    "GUARDIAN_SOURCE_RECEIPT_REQUIRED",
                    "EXACT_SOURCE requires at least one SIR_ receipt",
                )
            step_record["source_receipts"] = [
                self._validate_source_receipt(value) for value in sir_refs
            ]

        if normalized_step == "PROVE":
            frozen_sources = _normalize_sources(sources)
            payload["frozen_sources"] = frozen_sources
            step_record["frozen_sources"] = frozen_sources

        payload["steps"].append(step_record)
        payload["current_step"] = normalized_step
        payload["next_step"] = self._next_after(normalized_step)
        payload["updated_at"] = step_record["at"]
        if normalized_step == "FRESH_RUN":
            payload["closed"] = True
        self._save(payload)
        return self._view(payload)

    def assert_checkout(
        self,
        guardian_id: str,
        *,
        sources: Iterable[str],
        package_sha256: str,
    ) -> dict[str, Any]:
        payload = self._load(guardian_id)
        if payload.get("closed"):
            raise WorkshopError("GUARDIAN_CLOSED", f"guardian session is closed: {guardian_id}")
        self._assert_memory_stable(payload)
        self._assert_package(payload, package_sha256)
        if payload.get("current_step") != "PROVE" or payload.get("next_step") != "PATCH":
            raise WorkshopError(
                "GUARDIAN_PATCH_NOT_AUTHORIZED",
                "coding is blocked until HYPOTHESIS -> FALSIFIER_1 -> FALSIFIER_2 -> MAP -> "
                "COUNTERPROBE -> EXACT_SOURCE -> PROVE has completed in order",
                details=self._view(payload),
            )
        requested = _normalize_sources(sources)
        frozen = list(payload.get("frozen_sources") or [])
        if requested != frozen:
            raise WorkshopError(
                "GUARDIAN_SCOPE_MISMATCH",
                "Workshop checkout scope must exactly match the scope frozen by PROVE",
                details={"proven": frozen, "requested": requested},
            )
        if payload.get("bound_transaction"):
            raise WorkshopError(
                "GUARDIAN_ALREADY_BOUND",
                f"guardian is already bound to {payload['bound_transaction']}",
            )
        return self._view(payload)

    def bind_checkout(
        self,
        guardian_id: str,
        *,
        transaction_id: str,
        package_sha256: str,
        memory_refs: Iterable[str],
        purpose: str,
    ) -> dict[str, Any]:
        payload = self._load(guardian_id)
        self._assert_memory_stable(payload)
        self._assert_package(payload, package_sha256)
        if payload.get("current_step") != "PROVE" or payload.get("next_step") != "PATCH":
            raise WorkshopError(
                "GUARDIAN_PATCH_NOT_AUTHORIZED",
                "guardian is not at the PROVE -> PATCH boundary",
            )
        memory_refs_list = _normalize_refs(memory_refs, label="PATCH")
        now = utc_now()
        payload["bound_transaction"] = transaction_id
        payload["steps"].append(
            {
                "step": "PATCH",
                "at": now,
                "summary": str(purpose).strip(),
                "summary_sha256": sha256_text(str(purpose).strip()),
                "memory_refs": memory_refs_list,
                "evidence_refs": [transaction_id],
            }
        )
        payload["current_step"] = "PATCH"
        payload["next_step"] = "WORKSHOP"
        payload["updated_at"] = now
        self._save(payload)
        return self._view(payload)

    def status(self, guardian_id: str, *, full: bool = False) -> dict[str, Any]:
        payload = self._load(guardian_id)
        self._assert_memory_stable(payload)
        result = self._view(payload)
        if full:
            result["memory"] = payload["memory"]
            result["steps"] = payload["steps"]
        return result
