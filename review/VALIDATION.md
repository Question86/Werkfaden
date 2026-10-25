+++
schema = "kairos-context/v1"
id = "WERKFADEN_JEV_PORT_VALIDATION"
type = "documentation"
revision = 2
state = "active"
authority = "execution_evidence"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/WERKFADEN_JEV_PORT_VALIDATION"
updated_at = "2026-10-24T15:36:25Z"
capsule = "Fresh 262-test regression, canonical framework-source repair, deterministic index rebuild and isolated wheel verification."
claim_boundary = "Mocks verify plumbing only; no real AI review, production Apply or mandatory AI gate was executed."
entities = ["KAIROS", "WERKFADEN_JEV_PORT_VALIDATION"]
facets = ["tests", "jev", "guardian", "metadata", "source-binding", "packaging"]
criteria = []
does_not_answer = ["AI semantic accuracy", "production readiness"]
[[answers]]
intent = "validation"
question = "Which tests validate the JEV publication port and its limits?"
target = "s-overview"

[[answers]]
intent = "source_binding"
question = "How was the framework manifest repaired and checked against committed source bytes?"
target = "s-source-binding"
+++

# Executed validation

## CONTEXT INDEX

- `s-overview` — Fresh isolated suites, packaging checks and exact execution boundaries.
- `s-source-binding` — Reproduce the original CRLF/LF defect and rebuild the source-bound manifest.
- `s-ai-limits` — Simulated API tests and remaining AI enforcement gaps.

<a id="s-overview"></a>
## Overview

> Capsule: All four final suites pass in a fresh isolated checkout; provider and credential access are mocked.

Validation date: 2026-10-24. Windows, Python 3.13.5. The publication is based on `8a933430438f7478bf3a9aa34dc99d3864554452`, retains its existing review code and Guardian/metadata integration, and repairs the framework manifest's canonical source bindings.

| Suite / check | Final executed result |
| --- | --- |
| `python -m unittest discover -s review/tests -v` | 13 tests, PASS. Actual transport/attempt entrypoints execute with simulated provider responses and credential lookup. |
| `python -m unittest discover -s workshop/tests -p 'test_*.py' -v` | 65 tests, PASS, including Guardian boundaries, metadata afterchecks, context transactions, Shadow, rollback and postcheck. |
| `python -m unittest discover -s kairos/kairos_harness/tests -p 'test_*.py' -v` | 148 tests, PASS, including framework bootstrap/source verification and the Guardian retrieval bridge. |
| `python -m unittest discover -s kickstart/tests -p 'test_*.py' -v` | 36 tests, PASS. |
| Native framework index build with `--verify-determinism --compare-content-with ...` | PASS. 24 documents, ten gold queries; deterministic database bytes and unchanged logical content. |
| Framework manifest against canonical Git source blobs | PASS. All 24 source hashes match the bytes Git actually stores, including the three repaired entries. |
| `python scripts/v3_write_boundary_audit.py` | PASS. No contract failures or write findings. |
| Offline Workshop and harness wheel builds | PASS. Both built with `--no-index --no-build-isolation --no-deps` from small package-only copies. |
| Imports from extracted wheels under `python -I` | PASS. Guarded engine/CLI, metadata cores, Guardian bridge and search-policy loader import outside the source checkout; bundled framework hash verification succeeds. |

There are 262 distinct tests across the four primary suites. Each final suite completed without failures or errors after the manifest repair. The two final validation processes used separate temporary directories; no test targeted the productive KAIROS workspace, Runtime, metadata session, active lease or project database. Full local logs and source/remote hash evidence are outside the published repository.

The first sandboxed attempt was prevented from accessing newly created temporary directories by Windows sandbox permissions (`WinError 5`). The same tests were then executed with host filesystem access and isolated fixture directories. The subsequent fresh-checkout harness run exposed the actual source-binding defect described below. Neither a test assertion nor a gate rule was relaxed.

The existing bootstrap tests already detect the defective manifest; no duplicate implementation-mirroring test was added. The new branch is included in the existing offline CI workflow. GitHub Actions execution is separate evidence and is not presumed from local success.

<a id="s-source-binding"></a>
## Source binding repair

> Capsule: The native builder regenerates three LF source hashes while preserving the database and all integrity checks.

At the original published parent, `docs/WORKSHOP.md`, `workshop/README.md` and `workshop/OPERATIONS.md` were delivered by Git as LF, but the bundled manifest contained their CRLF hashes. Converting each committed LF document to CRLF reproduces its old expected hash exactly. Native framework verification therefore raised `framework source SHA-256 mismatch` in a fresh checkout. One failing test and eleven error/subtest reports in the initial 148-test harness invocation arose from that binding mismatch.

`python scripts/build_framework_index.py --verify-determinism --compare-content-with <independently rebuilt parent database>` was run using the actual canonical checkout. It regenerated the manifest through the supported build path. Only the three affected source hash entries change. `framework.db` remains byte-identical at SHA-256 `86e52e3352a2db2fd0a388a416de84306648a50ba05e88952c886193d7672c9e`; the database's logical content and retrieval answers also remain unchanged. The manifest and databases were not hand-edited.

All 24 new source bindings were additionally compared with canonical Git blobs, so staging's line-ending normalization cannot silently recreate the original mismatch. The final complete harness suite then passed all 148 tests.

<a id="s-ai-limits"></a>
## AI coverage and remaining limits

> Capsule: Offline tests prove the existing request route and explicitly characterize missing schema, approval-binding and Apply enforcement.

No real TypeSafe/JEV request was sent, no actual credential was retrieved, and no productive Apply was executed. Provider traffic is mocked. Tests verify the real `model/state/questions` envelope, direct adapter invocation, materialized before/after candidate bytes, transaction/config association, timeout/error propagation, separate proposition/evidence questions, candidate-fingerprint changes, context-specific cache, missing-score refusal and existing standalone decision-vector behavior. These tests establish plumbing, not the semantic accuracy of an AI review.

Characterization tests deliberately demonstrate remaining defects: a supplied review can be reused across candidate bytes within the same transaction; invalid numeric scores are not strictly rejected; a real isolated Workshop fixture can reach `POSTCHECK_VERIFIED` without a JEV artifact. Passing those tests does not implement or certify a mandatory AI gate. Local review routing and Workshop authorization remain distinct.

Guardian is deterministic and does not contact the review provider. Guardian-required normal context-document scope remains unsupported and fails closed; unguarded context transactions and separately proven auxiliary transactions retain their existing contracts. No thresholds, integrity comparisons, Guardian rules, metadata obligations or Apply paths were weakened for this publication.
