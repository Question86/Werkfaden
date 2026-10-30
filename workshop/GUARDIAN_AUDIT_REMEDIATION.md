+++
schema = "kairos-context/v1"
id = "KAIROS_GUARDIAN_AUDIT_HISTORY"
type = "documentation"
revision = 1
state = "ready"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_GUARDIAN_AUDIT_HISTORY"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Historical Guardian v1 audit/remediation classification with explicit security and verification non-claims."
claim_boundary = "Retains the recorded audit design map; it does not establish a current passed regression matrix or current completion of every remedy."
entities = ["KAIROS", "KAIROS_GUARDIAN_AUDIT_HISTORY"]
facets = ["framework-documentation", "historical-evidence"]
criteria = []
does_not_answer = ["live project state", "unverified current release acceptance"]
distribution_role = "historical_evidence"
origin_commit = "6d8d498c62c411ecad9c49e1ab41e2a72b724e56"
origin_source_sha256 = "f9cf58c13e957ff194b897d60071c539bb52dd436f9ebf3474cfe2241253664e"

[[answers]]
intent = "historical_evidence"
question = "How was the historical Guardian v1 audit classified and remediated?"
target = "s-remediation-rule"

[refs]
current_rules = "[ref:AGENTS.md#s-overview|id:KAIROS_FRAMEWORK_RULES|v:dynamic|rel:requires|tags:current,authority|src:framework]"

[[search_contract]]
query = "How was the historical Guardian v1 audit classified and remediated?"
expected = "KAIROS_GUARDIAN_AUDIT_HISTORY#s-remediation-rule"
required_top_k = 5
+++
# Guardian v1 audit remediation map

## CONTEXT INDEX

- [Remediation rule](#s-remediation-rule)
- [Disposition by audit group](#s-disposition-by-audit-group)
- [Explicit non-claims](#s-explicit-non-claims)


Historical source material captured from commit `6d8d498c62c411ecad9c49e1ab41e2a72b724e56`. Statements about release, rollout, branch or tests below belong to that source epoch. Current operating contracts and verified current receipts own current behavior. This material does not authorize publication or future execution.

This document records how the 100-check Guardian audit was classified and remediated. It is not a score and does not claim that the repository-wide regression matrix has already passed.

<a id="s-remediation-rule"></a>
## Remediation rule

> Capsule: Historical remediation distinguishes bounded hardening from replacement at the owning architectural boundary.

- **Principally right → minimal hardening at the existing owner.**
- **Structurally wrong → replace at the owning architectural boundary.** The displaced v1 source is disabled as a supported surface and retained byte-for-byte under `quarantine/`.
- **Hostile-process boundary → do not fake an application fix.** Filesystem ACL/service identity and a later native-tool hook remain the owner.

<a id="s-disposition-by-audit-group"></a>
## Disposition by audit group

> Capsule: Records audit-group diagnoses and their architectural dispositions for that remediation epoch.

| Audit IDs | Diagnosis | Architectural disposition |
|---|---|---|
| 001,048,049,087 | Structurally wrong: Guardian lived above the mutation owner in CLI checkout. | Enforcement moved into public `WorkshopEngine`; lease acquisition, lease validation, transaction state writes and lease release are Guardian-aware for CLI and direct Python callers. v1 engine is quarantined private core. |
| 002,006 | Memory hash was right; canonical identity was missing. | Operator/config owns canonical Memory path; symlink/reparse and multi-link substitution are rejected; session binds exact path/size/SHA-256. |
| 003–005,008,009 | Per-state Memory intent was right but post-hoc references were structurally too late. | Two-phase state gate: `guard-enter` runs before the state action, resolves/hashes exact Markdown sections and returns bounded `memory_context`; completion consumes that exact `GST_...` ticket. |
| 007,010 | Live-state ownership was right; reset/supersession was missing. | Live facts remain live-owned; `guard-reframe` appends supersession and returns to MAP/HYPOTHESIS without rewriting history; fresh run closes old proof. |
| 011 | Hypothesis gate was right but unstructured. | HYPOTHESIS requires observation, invariant, suspected mechanism, expected owner and refutation fields. |
| 012–018,027,028,080 | Evidence stages were right; opaque strings were structurally wrong. | F1 consumes semantic SRR; F2 independent graph-routed SRR; MAP graph-routed SRRs; counterprobe explicit negative SRR/SIR. Receipt status, freshness, authority snapshot and graph-anchor/truncation properties are validated. |
| 019,021–026,029,030,093 | Source permit/search was right but insufficiently session/state bound. | KAIROS source policy is bridged to active Guardian state gates. `negative_proof` is admitted only at COUNTERPROBE; ordinary source escalation only at EXACT_SOURCE; exceptions are refused. SRR/SIR carry Guardian/state-ticket binding and later states reject replay/stale/cross-scope receipts. Native shell reads remain hook/ACL territory. |
| 020 | Structurally wrong: v1 could only move forward. | `guard-reframe` returns an idle session to MAP/HYPOTHESIS and preserves prior events as superseded history. |
| 031–039 | Structurally wrong: PATCH equaled one TX. | PATCH becomes a multi-TX patch session with available/consumed scope, one active TX, rolling package hash, no consumed-source reopening and explicit patch close. |
| 040,055–058 | Workshop had useful mechanical scope; causal scope model was incomplete. | `werkfaden-patch-scope/v1` covers normal source/header/tests, auxiliary documents, static source-set operations and header-authority operations. Generated ledgers/topology/machine authority remain derived/work-package-hashed. |
| 041–045,067–070 | Structurally wrong: only checkout was guarded. | Every lease-requiring command validates exact active Guardian TX. Every state write is observed. Apply is blocked until prepared actual scope fits PROVE; terminal release consumes/retains scope from real Workshop state. |
| 046 | File scope was principally right but too coarse. | Exact-source match lines create edit windows; baseline→work diff hunks outside those windows fail before live apply. |
| 047 | Already correct. | Existing verified work-package hashing is retained unchanged in the quarantined synchronization core. |
| 050,064–066 | Checkout binding/crash window structurally wrong. | Guardian reservation precedes checkout; lease acquisition binds TX before worktree return; session lock/hash-chain serializes ledger. `guard-reconcile` repairs only provable bind/state/terminal crash windows and never adopts drift. |
| 051–053 | Structurally wrong mutation-class coverage. | Normal, auxiliary, static source-set and authority-migration transactions all enter the same patch-session engine boundary. |
| 054 | Maintenance boundary rather than ordinary patch. | Bootstrap repair is refused while Guardian enforcement is active; it remains pre-seal maintenance/recovery. |
| 059,060 | Already correct. | Existing topology and build-authority fail-closed rules are retained. |
| 061 | Existing global lease/package drift was right. | Retained; Guardian additionally serializes one active TX per patch session. |
| 062,063,099 | Ledger concurrency/history insufficient. | Cross-process per-session lock, atomic replacement and prev-hash/event-hash history. This is tamper-evidence, not authentication against the same unrestricted OS writer. |
| 068 | Existing drift checks were right. | Rolling package identity makes live drift a Guardian session failure as well as Workshop failure. |
| 071 | Already correct. | Guardian reads project DB; heartbeat remains the projection writer. |
| 072,073,075,078 | Manual WORKSHOP/HEARTBEAT states were structurally wrong. | Manual completion evidence removed. Patch close validates actual POSTCHECK transactions and the exact verified final heartbeat row. |
| 074,076,094–096,098 | Fresh-run free-form evidence structurally wrong. | Content-bound fresh-run receipt tied to Guardian/state ticket/final package/heartbeat/ledger head/executor/artifacts/problem evidence and configured validation labels. |
| 077 | Prior audit UNKNOWN. | Dynamic canonical freshness remains heartbeat/reconciliation authority and still requires live end-to-end re-audit; no static-code claim is made. |
| 079 | Graph design right; missing/unavailable graph must not masquerade as absence. | Structural evidence requires non-truncated graph-routed evidence with resolved authority anchor. Vocabulary deviations remain visible findings. |
| 081 | Already correct for canonical intake config. | Existing project-intake config binding retained. |
| 082 | Structurally wrong alternate control plane. | Public engine rejects a non-canonical config when `<workspace>/.kairos/workshop.config.json` exists. |
| 083 | Deployment/OS boundary. | Remains `SECURITY_BOUNDARY.md` / ACL / service identity / future hook. |
| 084–086,090 | Mixed application and hostile-writer issues. | Application state is hash/lease/package guarded; direct forged local DB/state/source writes by the same unrestricted OS identity are explicitly not claimed fixed. |
| 088,089 | Older governance is a different layer. | Governance continues to own semantic/workflow commands; Workshop package drift invalidates a Guardian mutation chain. Standalone authority-document governance remains separately auditable. |
| 091,092 | Already correct; strengthened. | Closed Guardian cannot reopen; fresh-run receipt is session/head/package bound; next investigation starts from fresh evidence. |
| 097 | Already correct. | POSTCHECK remains synchronization proof only. |
| 100 | Structurally missing adversarial matrix. | Focused v2 tests cover state preflight, multi-TX rolling scope, abort/no-consumption, direct engine/control-plane bypass, and KAIROS source-policy gate behavior. These are **not** a substitute for the complete 100-transition adversarial matrix or repository-wide regression suite; release test census must be updated only from an actual full CI run. |

<a id="s-explicit-non-claims"></a>
## Explicit non-claims

> Capsule: The recorded Guardian repair does not establish OS isolation or unrestricted-process resistance.

Guardian v2 closes the supported application mutation/source-escalation paths. It does not make an unrestricted same-identity process unable to edit source, SQLite, seals, leases, configuration or Guardian JSON. That stronger authority is filesystem ACL/service identity plus a later native-tool hook.

Project-specific build/runtime/scientific correctness is likewise not invented by the generic framework. Guardian can require content-bound validation receipts by configured label, but the project-specific runner/adapter remains the owner of those claims.
