+++
schema = "kairos-context/v1"
id = "KAIROS_JEV_APPLY_POLICY"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_JEV_APPLY_POLICY"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Conditional evidence-bound JEV Apply admission, workflow binding and explicit experimental limits."
claim_boundary = "Defines an explicitly configured policy and its refusal conditions; it is not blanket deployment authority, external review correctness or default project approval."
entities = ["KAIROS", "KAIROS_JEV_APPLY_POLICY"]
facets = ["framework-documentation", "current-authority"]
criteria = []
does_not_answer = ["live project state", "unverified current release acceptance"]
distribution_role = "current_authority"
origin_commit = "6d8d498c62c411ecad9c49e1ab41e2a72b724e56"
origin_source_sha256 = "b313047a537e6018c06ad03782fc56172ea507795a67f332efd1391b3e92c714"

[[answers]]
intent = "procedure"
question = "How does the optional JEV Apply policy bind an exact Workshop candidate?"
target = "s-correct-usage"

[refs]
current_rules = "[ref:AGENTS.md#s-overview|id:KAIROS_FRAMEWORK_RULES|v:dynamic|rel:requires|tags:current,authority|src:framework]"

[[search_contract]]
query = "How does the optional JEV Apply policy bind an exact Workshop candidate?"
expected = "KAIROS_JEV_APPLY_POLICY#s-correct-usage"
required_top_k = 5
+++
# JEV candidate binding: experimental Apply policy

## CONTEXT INDEX

- [Correct usage](#s-correct-usage)
- [Limitations / not yet secure enough for production release authority](#s-limitations-not-yet-secure-enough-for-production-release-autho)
- [Transaction lifecycle and retention](#s-transaction-lifecycle-and-retention)


This change adds a *conditional* (explicit `jev_apply_required: true`) JEV
admission check to the ordinary public `WorkshopEngine.apply` path. Existing
projects remain unchanged until this policy is deliberately enabled.

<a id="s-correct-usage"></a>
## Correct usage

> Capsule: Documents explicit review inputs and exact active-transaction submission; an attempt-review file is not approval.

1. Run a fresh TypeSafe attempt review with the exact Workshop candidate.
2. Run the standalone JEV workflow using an explicit positive and negative
   question policy. Do not reuse `--attempt-review` as approval evidence.
3. Preserve the workflow JSON in a distinct, immutable path for each iteration.
4. With the verified transaction still active, run:
   `python review/jev_bind_workflow.py <workflow-json> --transaction-root <TX-root>`
5. The binder writes `review/jev_workflow.json` once (never overwrites).
   Enable `jev_apply_required=true` in the project Workshop config *before
   checkout/seal*, with all governance and configuration authority requirements
   handled by the normal Workshop lifecycle.
6. `apply` rejects a missing receipt, a foreign TX, changed Work bytes,
   a mismatched Workshop package, empty policies, invalid scores, or a low
   JEV vector. All the existing metadata/shadow/scientific gates remain.

<a id="s-limitations-not-yet-secure-enough-for-production-release-autho"></a>
## Limitations / not yet secure enough for production release authority

> Capsule: Local review JSON is not signed provider provenance or independent production release authority.

- The TypeSafe receipt is **not provider-signed**. A local JSON file is not a
  cryptographic proof of response provenance or an independent AI identity.
- The workflow is a pre-execution patch-attempt assessment; it does not prove
  deployment, tests, scientific correctness, or full semantic support.
- The JEV attempt transport currently sends selected code changes; the fingerprint
  covers the full work tree. A passed review does not prove Jev saw every Markdown
  file in that tree.
- The ordinary `WorkshopEngine.apply` is gated only when explicitly configured.
  Authority migrations, auxiliary and source-set apply routes need independent
  mandatory review integration. Do not advertise a universal JEV admission gate.
- Rebuilding the verified package in Apply is necessary but does not provide an
  independent immutable attestation of the external workflow content.
- Missing historical receipts require new bounded reviews; old JSON must not be
  silently upgraded to approval.

<a id="s-transaction-lifecycle-and-retention"></a>
## Transaction lifecycle and retention

> Capsule: Explains separately configured transaction-document and retention obligations; presence alone does not prove activation.

A separate opt-in lifecycle implementation exists on this feature branch:

- `transaction_documents_required=true`: checkout and terminal states
  publish one `type=transaction` Markdown source in the existing managed
  `reports/` root. The document follows the 18 fixed implementation-method
  anchors; sections without proven knowledge say `none`. The normal KAIROS
  heartbeat validates and projects it into existing SQLite artifacts,
  revisions, sections, FTS and relations (no new tables required).
  Document publication failures are recorded in durable pending markers;
  new work is blocked until `tx-document-reconcile` succeeds. Apply is
  **never repeated** solely to repair a transaction document.
- `tx_retention_required=true`: implies the document policy must also be
  enabled. A post-verify candidate snapshot is stored as SHA-addressed
  immutable blobs. Before a new checkout, a configurable
  `tx_max_materialized=10` cap makes the pool compact only terminal,
  projected records. All transaction bytes are saved and re-read before
  large working directories are discarded. If no safe eviction exists,
  checkout fails closed rather than deleting protected candidate work.
- `tx-note` records explicitly attributed findings, failed approaches,
  inherited atomic references and next actions. Entries are declared
  observations, **not verified gate evidence or deployment receipts**.
- `tx-pool-status`, `tx-compact`, `tx-rehydrate` expose the retention
  and restoration operations. The vault does not garbage-collect shared
  content and it does not grant a new candidate a former candidate's
  verification.

All three flags default to **false** for compatibility with existing
installed projects. Do not enable on an existing project instance before
verifying the exact local environment, migrations, recovery paths and required
provider credentials. Existing historical TX are not automatically imported.

Current limitations: only the ordinary public Apply has the opt-in JEV
admission check; other mutation kinds still require separate JEV integration.
JEV responses are unsigned local JSON and cannot supply cryptographic
provider provenance. The document path records the Workshop's own postcheck,
not process/binary deployment. Automatic extraction of *sub-patch atomics*
from a combined candidate, smart relevance selection, provider-attested
JEV responses, cross-project migrations and shared-vault object garbage
collection remain open.
