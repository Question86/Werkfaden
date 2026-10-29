from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable

from .corpus import WorkshopConfig
from .context_documents import ContextDocuments
from .metadata_gate import MetadataGate
from .scientific import ScientificParityGate


class EngineCoreContract(ABC):
    """Typed private composition contract; no state storage or mutation implementation.

    Responsibility classes provide every concrete method. The guarded public
    engine remains the supported construction and mutation authority.
    """

    config: WorkshopConfig
    context_documents: ContextDocuments
    metadata_gate: MetadataGate
    scientific_gate: ScientificParityGate

    @abstractmethod
    def __init__(self, config_path: Path) -> None:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _acquire_lease(self, transaction_id: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _assert_live_baseline(self, state: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _assert_metadata_checked(self, root: Path, state: dict[str, Any], work_sha256: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @staticmethod
    @abstractmethod
    def _authority_sha256(value: str, label: str) -> str:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _backup_apply_targets(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> list[dict[str, str]]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _fixture_promote_live(self, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> None:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _import_kairos(self) -> tuple[Any, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @staticmethod
    @abstractmethod
    def _intake_fact(*, live_path: Path, staged_path: Path) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _load_authority_record(self, *, record_id: str, directory: str, expected_type: str, expected_states: set[str], expected_sha256: str, label: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _load_transaction(self, transaction_id: str) -> tuple[Path, dict[str, Any]]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _metadata_work_manifest(self, root: Path, state: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _mirror(self, manifest: dict[str, Any], *, documents: Iterable[str]=()) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _object_path(self, digest: str) -> Path:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _prepare_topology_authority(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any], *, stage: bool=True) -> dict[str, Any] | None:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _release_lease(self, transaction_id: str) -> None:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _render_header_ownership_blueprint(self, source_text: str, owners: set[str], *, updated_at: str, advance_revision: bool, path: Path) -> str:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _render_include_ownership_authority(self, source_text: str, owners: dict[str, set[str]], *, updated_at: str, include_edges: list[dict[str, Any]] | None=None, topology_sha256: str | None=None) -> str:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _require_lease(self, transaction_id: str) -> None:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _review_map(self, root: Path, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _rollback_files(self, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _save_transaction(self, root: Path, state: dict[str, Any], *, event: str, details: Any=None) -> None:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _shadow_fixture(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _shadow_native(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _stage_project_intake_authority(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _store_object(self, raw: bytes) -> str:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _test_changes(self, root: Path, state: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _transaction_path(self, transaction_id: str) -> Path:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _verify_authority_scope(self, *, decision_id: str, decision_sha256: str, exception_report_id: str, exception_report_sha256: str, blocker_report_id: str, blocker_report_sha256: str) -> dict[str, dict[str, Any]]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _verify_checkout_live_scope(self, root: Path, state: dict[str, Any], baseline_manifest: dict[str, Any], *, error_code: str) -> list[str]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def _write_receipt(self, kind: str, payload: dict[str, Any]) -> Path:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def abort(self, transaction_id: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def apply(self, transaction_id: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def authority_recover(self, transaction_id: str, *, decision_id: str, decision_sha256: str, exception_report_id: str, exception_report_sha256: str, blocker_report_id: str, blocker_report_sha256: str, expected_current_package_sha256: str, expected_sealed_package_sha256: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def checkout(self, sources: Iterable[str], *, purpose: str, tests: Iterable[str]=(), documents: Iterable[str]=()) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def include_documents(self, transaction_id: str, documents: Iterable[str], *, reason: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @property
    @abstractmethod
    def lease_path(self) -> Path:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def metadata_check(self, transaction_id: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def metadata_plan(self, transaction_id: str, *, refresh: bool=False) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def prepare(self, transaction_id: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def recover(self, transaction_id: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def scientific_baseline(self, transaction_id: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def scientific_reproducibility(self, transaction_id: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def scientific_stage_runner_authority(self, transaction_id: str, *, decision_id: str, expected_runner_sha256: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def scientific_verify(self, transaction_id: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def seal(self) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @property
    @abstractmethod
    def seal_path(self) -> Path:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def stale_abort(self, transaction_id: str, *, expected_current_package_sha256: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def status(self, *, persist: bool=True) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def transaction_status(self, transaction_id: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')

    @abstractmethod
    def verify(self, transaction_id: str) -> dict[str, Any]:
        raise NotImplementedError('provided by the composed responsibility owner')
