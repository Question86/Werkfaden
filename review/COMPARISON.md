+++
schema = "kairos-context/v1"
id = "WERKFADEN_JEV_PORT_COMPARISON"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/WERKFADEN_JEV_PORT_COMPARISON"
updated_at = "2026-10-23T23:47:06Z"
capsule = "Local JEV submission modules and GitHub branch differences with verified enforcement gaps."
claim_boundary = "This comparison identifies the port and its tested limits; it does not certify AI approval or a live deployment."
entities = ["KAIROS", "WERKFADEN_JEV_PORT_COMPARISON"]
facets = ["jev", "ai-review", "limitations"]
criteria = []
does_not_answer = ["live project approval", "runtime correctness"]
[[answers]]
intent = "ai_review"
question = "Local JEV submission modules and GitHub branch differences with verified enforcement gaps."
target = "s-overview"
+++

# JEV review port and verified comparison

## CONTEXT INDEX

- `s-overview` — Deployed local review modules, branch differences and exact authority limits.

<a id="s-overview"></a>
## Overview

Comparison date: 2026-10-24. The task authorizes an isolated branch publication, not a live installation upgrade. No local Runtime, Workshop configuration, lease, seal or source was changed. A read-only SQLite snapshot and copied source were used for inspection; the original workspace refused routing access at its lock.

| Component | Local implementation | GitHub before this port | Difference / action |
| --- | --- | --- | --- |
| Installation entry | `runtime_sync_workshop/workshop.py` adds adjacent `src`; installed binding names `e365aff00ba77cad942f062e474258cdd4b0122e` and a local softfix release | `feat/workshop-context-transactions` at that commit | Binding is not byte identity: seven Python files differ and four additional compatibility/test modules exist locally. Those project compatibility modules are outside this review port. |
| Direct AI transport | Root-level `jev_transport.py`; fixed TypeSafe `/v1/systemone`, model `jev-latest`, Windows Credential Manager, bounded retries | Absent in the four branches checked below | Port unchanged, including credential setup helper; standard library only. |
| Patch-attempt submission | `jev_workshop_attempt_gate_typesafe.py` plus compatibility module | Absent | Port actual changed-region materialization and `noul` questions; remove private project questions and require an explicit profile. Forward the chosen config to transaction-state lookup. |
| Reconsideration workflow | `jev_workshop_attempt_workflow.py` | Absent | Port candidate fingerprint, threshold vector, failed-attempt reentry guard and subprocess limits. Require explicit existing positive/negative policy flags rather than publishing private defaults. |
| Atomic support/evidence review | `jev_adaptive_patch_tree.py`, `how_to_jev.md`, tomography procedure | Absent | Port separate support and sufficiency questions, context-keyed cache, retrieval/decomposition and diagnostic nodes. Keep the compatibility import adapter. |
| Metadata aftercheck | Local `metadata_gate.py` exactly equals `feat/workshop-context-transactions` after line-ending normalization | Present on `feat/workshop-context-transactions`; absent on `v3` and `v3-axiom-atlas-loop` | Preserve plan, audit, candidate hash, aftercheck, Shadow and postcheck. |
| Deterministic Guardian v2 | Not imported by the copied local Workshop engine | Present on `v3-axiom-atlas-loop` | Merge Guardian wrappers, receipt/state/evidence modules, bridge, templates and tests; execute the newer metadata-aware core behind the existing restricted loader. Retain historical v1 files. |
| Typed API / validated verdict | Python dictionary annotations and provider `type: noul` requests; partial object/numeric checks | No JEV client | No strict response schema, signed approval, general Findings contract or verified model pin exists locally. Do not claim otherwise. |
| AI decision at Apply | No JEV reference in the copied Workshop package; standalone workflow explicitly says `live_apply_authorized=false` | Also absent | Local gap remains. This task ports existing code; it does not invent a mandatory AI gate. |

Branches examined and fetched in an isolated clone:

- `feat/workshop-context-transactions`: `e365aff00ba77cad942f062e474258cdd4b0122e` (publication base).
- `v3-axiom-atlas-loop`: `1d0bcf7f411630586104c8d6f420662bce8ac41a` (Guardian merge parent).
- `v3`: `6e9d4b1fd43edfe768385015fba36af47d492b6a` (remote default).
- `main`: `7ec51198761d1b12796a7fb655771ff06137fb9a`.

A content search across Python, TOML and JSON at all four heads found no TypeSafe/systemone transport, review module or support/sufficiency implementation. The isolated clone fetched the full remote branch inventory. No assumption was made that the default branch contained the other branches' work.

## Actual call and decision path

`jev_workshop_attempt_workflow.py` -> attempt script subprocess -> Workshop transaction-state lookup -> actual existing live/candidate source comparison -> `jev_transport.call_jev` -> fixed TypeSafe endpoint -> JSON object -> attempt scores -> workflow vector. A low vector routes reconsideration and optional adaptive tomography. Tomography sends stable node IDs and separate `_E` sufficiency questions, then retrieves and decomposes evidence.

The workflow records a hash over `work/runtime` and `work/tests`, response-file hash and transaction identity. Its previous-low-review continuation requires changed candidate bytes and a revision note. This is a reentry guard, not a cryptographic approval authority.

No normal `apply`, authority migration `apply`, static source-set `apply` or auxiliary `apply` consumes a JEV response. Existing lease/seal, metadata, Shadow, scientific (when configured), heartbeat and postcheck checks remain their own gates. Guardian v2, when required in canonical configuration or forced by its existing environment switch, enforces investigation/session authority below the CLI; it is deterministic and does not call JEV.

## Verified limitations

- `--attempt-review` accepts a supplied review with matching transaction ID without proving that its scores describe the current candidate. A regression test demonstrates candidate-A result reuse for candidate B. The emitted fingerprint does not cure that gap.
- Numeric response handling accepts booleans and clamps out-of-range scores. Full response/request schema validation and provider-authenticated findings are absent. Non-object transport responses and missing tree scores fail; that is a narrower guarantee.
- The model remains an alias. A receipt is not a pinned model-version attestation.
- The attempt CLI intentionally omits blueprints, graph receipts and `current.json` from the transmitted state. Default context is exact changed lines, limited to 48 KiB state. Provider acceptance does not prove context sufficiency.
- Candidate discovery covers existing `runtime/` and `tests/` text counterparts. It is not a general static-source-set/create/delete/rename/authority migration reviewer. Standalone support/evidence scores are not test coverage or scientific qualification.
- Request failure raises/stops the standalone route. The Workshop can still apply a mechanically verified fixture without running this route, because no AI policy is enforced there.
- Guardian v2 has no normal-transaction context-document scope. Combined `checkout --document` and `include-documents` fail closed while Guardian is required; use a separately proven auxiliary transaction. The newer unguarded context workflow and metadata review remain available. Expanding Guardian's scope contract would be separate work.
- Existing negative-vector comparison uses floating-point `1-threshold`; the exact decimal boundary can reflect floating-point representation. No thresholds or decision rules were changed to make the port pass.

## Port boundaries and dependencies

Six adjacent Python modules under `review/`, one question-profile example, local source hashes, tests and operating documentation are shipped. Private project question wording/default policy, product packets, local databases, keys, credentials and productive review results are excluded. The source provenance manifest records original file hashes; changed files have the portability adaptations listed above. Credential setup is explicit and interactive; no credential is read during these tests.

Guardian code is merged from already published sources. The existing guarded loaders now read `engine_metadata_v2.py.txt` and `cli_metadata_v2.py.txt`, exact metadata-branch synchronization cores, instead of rolling synchronization back to the old v1 core. Historical v1 quarantined files remain unchanged. Package data includes those required `.txt` cores.

## Validation

See `VALIDATION.md` for executed suites and the distinction between simulated provider responses and a real AI request. Native fixture Apply succeeds through actual metadata, Shadow and postcheck checks without a JEV artifact; this positively demonstrates the missing AI enforcement boundary. Publishing this port does not establish an operational mandatory AI gate.
