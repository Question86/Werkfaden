+++
schema = "kairos-context/v1"
id = "KAIROS_WORKSHOP"
type = "documentation"
revision = 6
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_WORKSHOP"
updated_at = "2026-10-25T16:00:00Z"
capsule = "Authoritative Workshop lifecycle for jointly updated code and Markdown, with required semantic header changes, evidence-backed metadata afterchecks, hash-bound apply clearance and heartbeat projection."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_WORKSHOP"]
facets = ["ai-review", "guardian", "workshop", "transaction", "semantic-context", "metadata-aftercheck", "source-set", "topology", "authority-migration"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "workshop"
question = "How do I modify governed code through the KAIROS Workshop?"
target = "s-overview"

[[answers]]
intent = "topology"
question = "How does the Workshop handle include-owner topology changes?"
target = "s-header-and-topology-behavior"

[[answers]]
intent = "migration"
question = "When is KAIROS authority migration required?"
target = "s-authority-migration"

[[answers]]
intent = "migration"
question = "When is authority migration required?"
target = "s-authority-migration"

[[answers]]
intent = "source_set"
question = "How do I create delete or rename governed source files after seal?"
target = "s-universal-source-set-mutation"

[[answers]]
intent = "semantic_context"
question = "How do I update Markdown context together with a code patch?"
target = "s-semantic-context-transactions"

[[answers]]
intent = "metadata_aftercheck"
question = "How must I check metadata before applying a source patch?"
target = "s-metadata-aftercheck"

[[search_contract]]
query = "How do I modify governed code through the KAIROS Workshop?"
expected = "KAIROS_WORKSHOP#s-overview"
required_top_k = 1

[[search_contract]]
query = "How do I update Markdown context together with a code patch?"
expected = "KAIROS_WORKSHOP#s-semantic-context-transactions"
required_top_k = 1

[[search_contract]]
query = "How must I check metadata before applying a source patch?"
expected = "KAIROS_WORKSHOP#s-metadata-aftercheck"
required_top_k = 1
[[answers]]
intent = "ai_review"
question = "Does Workshop enforce JEV AI approval before Apply?"
target = "s-standalone-ai-review"

+++
# Workshop module

## CONTEXT INDEX

- `s-standalone-ai-review` â€” JEV diagnostic by default and opt-in ordinary Apply admission; no universal AI or deployment approval.

- [`s-overview`](#s-overview) â€” The Workshop synchronizes governed source, implementation documents and reviewed Markdown context through one verified package.
- [`s-semantic-context-transactions`](#s-semantic-context-transactions) â€” A code patch includes its affected semantic Markdown before verification and applies that complete package before heartbeat promotion.
- [`s-metadata-aftercheck`](#s-metadata-aftercheck) â€” Every ordinary code patch requires changed semantic headers, field-by-field review and bound source/document spot checks before apply.
- [`s-header-and-topology-behavior`](#s-header-and-topology-behavior) â€” Header-only project records are valid Workshop checkout targets and can be changed through
- [`s-universal-source-set-mutation`](#s-universal-source-set-mutation) â€” Create, delete and rename of static-ecosystem source files use a separate candidate migration.
- [`s-authority-migration`](#s-authority-migration) â€” authority-checkout requires an isolated candidate project tree plus its compile_commands.json

<a id="s-overview"></a>
## Overview

> Capsule: The Workshop synchronizes governed source, implementation documents and reviewed Markdown context through one verified package.

The Workshop is a fail-closed synchronizer for a separately configured codebase, its
implementation documents and its KAIROS projection. It is shipped unbound and becomes
project-specific through universal intake. Static ecosystems carry bounded source membership;
C/C++/CUDA retains compiler-backed translation-unit/header authority.

The authority chain is: project goal/task scope, compiler-recorded translation-unit
membership, exact source/header mappings and ledgers, compiler-order static include closure,
identical external/managed implementation documents, a verified KAIROS database projection,
and a package hash covering the machine authorities. A mismatch blocks sealing, checkout,
preparation, verification or application rather than being repaired by assumption.

Normal operation is:

```text
status -> seal -> checkout -> edit code and metadata -> metadata-plan
-> complete audit -> prepare -> metadata-check -> verify -> apply
```

Work is edited only under the transaction `work/` tree. `prepare` regenerates mechanical
hash/ledger layers. C-family retains its token-stream review rule; static universal ecosystems do
not acquire fake C++ token semantics and advance their exact mechanical mirrors on byte changes.
In addition, every actually changed source/header in an ordinary patch, in every admitted language,
requires a semantic header change and the metadata aftercheck described below. This requirement
uses the actual byte changes and does not depend on the C++ token rule.
Mutable file facts have one mechanical owner: generated SOURCE MAPPING/ledger layers. Project
intake does not duplicate those changing hashes in an independently stale semantic section.

`metadata-check` grants a clearance for the prepared source/document/review package. `verify`
requires that clearance and promotes the prepared documents into an isolated native KAIROS shadow. The shadow
contains the authoritative task, method, source-index and other source documents referenced
by changed implementation documents, while derived live state and receipts remain isolated.

`apply` backs up exact targets, writes only the selected transaction result, runs the governed
heartbeat, verifies the complete corpus, reseals and releases the lease. A successful result
is `POSTCHECK_VERIFIED` with a bit-exact receipt.

<a id="s-semantic-context-transactions"></a>
## Semantic context transactions

> Capsule: A code patch includes its affected semantic Markdown before verification and applies that complete package before heartbeat promotion.

An ordinary checkout always includes each selected source's implementation Markdown under
`work/blueprints/`. Select additional affected existing workspace documents with repeatable
`checkout --document <workspace-relative.md>` arguments. They are copied, with their exact
baseline, into `work/documents/<workspace-relative.md>`. The agent reviews which documents are
affected by the behavior change; the Workshop does not infer that scope from filenames or claim
to discover every semantic dependency.

Edit the code and its context together in the transaction. Review `capsule`, `answers`, `refs`,
`claim_boundary`, `entities`, `facets`, `does_not_answer`, `search_contract` and the affected
sections, including their capsules and context index. A new
behavior may require new answer targets, narrower claims or revised relationships even when its
source mapping is mechanically correct. Preserve historical revision-pinned references when they
still describe the intended provenance; a patch does not automatically retarget all references.

The existing `metadata_review` records source/implementation-document impact. Additional selected
documents have a `document_review` at `review/context_review.json`. Every selected document needs
an explicit `updated` or `none` decision, a reason, reviewer, review time and all five entries in
`reviewed_fields`: `capsule`, `answers`, `refs`, `claim_boundary`, `sections`. `updated` requires a
real semantic edit; `none` requires unchanged semantic content. Revision and timestamp changes
alone do not count. An unchanged document can remain in scope after review without a forced
rewrite. These records attest the review; they do not prove that an LLM's interpretation is true.

If another affected document is found during work, use
`include-documents <transaction-id> --document <path> --reason "<scope reason>"`. This binds it to
the unchanged live baseline, preserves existing work, adds its review entry and invalidates any
prior preparation or verification. Complete the newly included document; for a code patch, refresh
the metadata plan and audit as needed, then run `prepare`, `metadata-check` and `verify` again.
Do not copy an extra file into the work tree as a substitute for declaring scope.

`prepare` advances changed document revisions and mechanical evidence. `verify` validates the
complete prepared document set in the isolated KAIROS shadow and binds it, with the reviews, to
the work-package hash. `apply` backs up and writes those exact reviewed bytes together with the
source change, then runs the governed heartbeat. The heartbeat updates the SQLite artifact
metadata, answer handles, section search and reference/graph projections from the new Markdown.
Postcheck and recovery include the selected documents. Editing a document or review after
verification requires a new preparation and verification; it cannot silently change the applied
package. No semantic rewrite is deferred until after apply.

This extension edits existing managed documents. It does not create or delete documents, bypass
source-set/authority migrations, or permit edits to generated routers, runtime documents or
machine-owned topology authority. Select a code document through its owning `--source`, rather
than adding `code/*.md` as a separate context document. Document-only auxiliary transactions remain
a separate interface for their configured external process authorities.

<a id="s-metadata-aftercheck"></a>
## Mandatory metadata aftercheck

> Capsule: Every ordinary code patch requires changed semantic headers, field-by-field review and bound source/document spot checks before apply.

Metadata maintenance is a required operation on the transaction candidate, not a task to defer
until after apply. For every source/header with changed bytes, the corresponding implementation
Markdown must change at least one real value in `capsule`, `answers`, `refs`, `claim_boundary`,
`entities`, `facets`, `does_not_answer` or `search_contract`. An unrelated body sentence, formatting,
revision, timestamp or regenerated source ledger cannot satisfy this header-change requirement.
Other correct field values may remain unchanged, but the audit must review and explain them.
The requirement applies to ordinary source/header patches in all admitted languages and to
existing sources/headers whose bytes change in an authority migration. A pure
Markdown transaction with unchanged code bytes keeps its existing review path and does not need
an artificial header edit.

After editing the candidate code and metadata, run `metadata-plan <transaction-id>`. It creates
`review/metadata_audit.json` with the bound review scope, field decisions and reproducible
source/document spot checks. The template contains pending work, not pre-approved findings.
Read the candidate source and the relevant document claims; fill in the decisions, concrete
explanations and requested evidence. Preserve the generated sample identities and evidence
locations. A review is incomplete if it only restates that the file was reviewed.

`prepare` validates the header-change obligation and the completed audit. Then run
`metadata-check <transaction-id>` to issue clearance for the prepared package. The check verifies
the declared field changes against the actual header values, the selected evidence and its
hashes/locations, the completed spot checks and an RFC-3339 review time. Missing or unresolved
findings block progress. Correct overbroad or contradicted claims and review the corrected
candidate before clearance; a partial finding is not a pass.

Both `verify` and ordinary `apply` require a current clearance bound to the candidate and review.
Changing source, semantic Markdown, selected scope or review after clearance makes that clearance
stale. Refresh the plan when its evidence changes, complete the audit again, and rerun `prepare`,
`metadata-check` and `verify`. Do not edit the receipt or state to reuse an earlier clearance.
The final heartbeat projects the already-reviewed Markdown; it does not repair its meaning.

The aftercheck follows the original blueprint method: derive claims from current source; retain
canonical IDs and typed references; use the narrowest supporting section; distinguish current
implementation facts from historical requirements and unresolved observations. State only
relationships proved by the cited source. A source/document sample identifies the exact claim,
source location, result and revision anchors. Its pass concerns that sample, not every claim in
the codebase. Preserve existing detailed evidence and one canonical representation of graph
metadata rather than adding duplicate facts or changing the context schema.

This gate makes the maintenance work and its evidence checkable. It is not an automatic proof
that an LLM's explanation is true, an independent reviewer identity check, a build/test runner or
a proof of complete semantic dependency discovery. It covers ordinary source/header patches
and existing-file code changes in authority migrations. New and removed files retain their
separate migration review; the gate does not invent a baseline header for a new file. Static
source-set migrations reject edits to retained existing source paths.

<a id="s-header-and-topology-behavior"></a>
## Header and topology behavior

> Capsule: Header-only project records are valid Workshop checkout targets and can be changed through

Header-only project records are valid Workshop checkout targets and can be changed through
the same synchronized lifecycle as translation units.

For kickstart-generated projects, the Workshop retains per-translation-unit include-root
sequences from compiler intake. It recomputes the current header-owner relation during
corpus checks instead of trusting the initial source index forever.

If a patch changes only which translation units own already-governed headers, `prepare`
regenerates the `s-include-closure` authority in `docs/PROJECT_SOURCE_INDEX.md`, advances its
KAIROS revision, includes it in the verified work-package hash, promotes it in the native
shadow, and applies/promotes it atomically with the implementation document.

If the governed header set itself changes, the ordinary transaction fails before live mutation
with `AUTHORITY_TOPOLOGY_MIGRATION_REQUIRED`. Changing the governed local-header set is intentionally not disguised as an ordinary patch; use the
explicit authority-migration lifecycle below. Translation-unit membership or normalized compiler-
context changes are stronger still and are refused by the generic migration with
`BUILD_AUTHORITY_MIGRATION_REQUIRED`.

<a id="s-universal-source-set-mutation"></a>
## Universal static source-set mutation

> Capsule: Create, delete and rename of static-ecosystem source files are Workshop-owned candidate transactions; the live project is unchanged until shadow-verified apply.

Python/JavaScript/TypeScript/Rust/Go/JVM/.NET/Ruby/PHP source-set changes use:

```text
source-set-checkout -> edit isolated candidate -> source-set-prepare -> source-set-verify -> source-set-apply
```

`source-set-checkout` copies the sealed governed project into Workshop transaction staging. The
agent creates/deletes/renames sources only inside that candidate. `source-set-prepare` recomputes the
bounded static source membership and generated Markdown/machine authority. `source-set-verify`
builds and verifies the candidate KAIROS/Workshop corpus in isolation. `source-set-apply` is the only
step allowed to alter live source membership; it advances source files, active/tombstoned Markdown,
machine authority, database projection and seal together, ending at `POSTCHECK_VERIFIED`.

An existing source path must retain its sealed bytes in a source-set candidate. Combining a
membership change with edits to an existing source fails with `ORDINARY_METADATA_PATCH_REQUIRED`
before document regeneration. Apply those existing-source edits through the ordinary metadata
workflow before or after the membership migration. Adding a source does not bypass the aftercheck
for an existing source patch.

The generic source-set transaction fails closed if build/config authority changes or C-family source/
header topology changes. Those transitions use stronger authority paths; the static source-set path
never rewrites build configuration or weakens compiler-backed C-family evidence.

<a id="s-authority-migration"></a>
## Authority migration

> Capsule: authority-checkout requires an isolated candidate project tree plus its compile_commands.json

```text
authority-checkout -> authority review -> authority-prepare -> authority-verify -> authority-apply
```

`authority-checkout` requires an isolated candidate project tree plus its `compile_commands.json`
and a matching sealed live baseline. The candidate tree is never treated as live; compiler-recorded
translation units and the proven local header closure are copied into transaction staging. The
transaction records header additions/removals and byte changes. It requires compiler translation-unit
membership and normalized compiler context to remain unchanged. A code-only candidate with unchanged
authority is refused and must use the normal Workshop; a changed source membership or compiler context
is refused until a project-specific build-authority migration is configured.

`authority-prepare` requires explicit review of every changed/created/removed governed file,
regenerates mechanical evidence, advances current derived compiler/include sections, builds a new
source index and stages machine authority. Removed managed implementation documents are retained as
`superseded`; they cease to be active external corpus members but remain addressable historical
evidence.

If existing source/header bytes change, update their semantic headers in `work/blueprints`, run
`metadata-plan`, and complete `review/metadata_audit.json` before `authority-prepare`. Run
`metadata-check` after preparation and before `authority-verify` and `authority-apply`. These
steps enforce the same actual header changes, field reviews, source/document samples and fresh
hash-bound clearance as an ordinary patch. Created and removed files keep the migration's
explicit creation/removal reviews. A metadata change after clearance requires renewed preparation,
metadata checking and verification; refresh the plan and audit when their evidence has changed.

`authority-verify` promotes the candidate document set into an isolated KAIROS shadow and requires
the staged Workshop corpus, database projection and exact ledgers to verify before live paths can
change. `authority-apply` backs up affected live code/documents/machine authority, applies the
verified topology, runs the KAIROS heartbeat and full health/corpus postcheck, and creates the new
seal. Work-package hashes prevent mutation after verification.

The migration consumes compiler evidence but does not infer edits to `CMakeLists.txt`, Meson, Bazel
or other project build sources. For that reason the generic migration does not accept changed compiler
context or translation-unit membership at all. Those transitions require a project-specific build-
authority migration that can modify or otherwise reproduce the real build source before KAIROS adopts
the new compiler database.

The optional parity interface is deliberately empty until a project supplies a reproducible
build/scientific/product-specific adapter. Mechanical synchronization is not promoted into
an application-domain correctness claim.

<a id="s-standalone-ai-review"></a>
## Standalone AI review and combined branch

> Capsule: JEV is diagnostic by default; ordinary Apply can require an explicitly bound candidate receipt without provider-signed attestation.

The publication combines Guardian v2 with the metadata-aware Workshop core. Existing deterministic gates retain their own scope. Optional JEV submission, separate evidence-sufficiency questions and reconsideration tools live under `review/`; see [the operating instructions](../review/README.md) and [the comparison](../review/COMPARISON.md). Ordinary `WorkshopEngine.apply` conditionally checks a bound JEV workflow only with `jev_apply_required=true`. It remains local, not provider-signed or a deployment receipt. Auxiliary, authority, bootstrap and source-set Apply require separate integration. A metadata reviewer field or Guardian receipt is not evidence of an AI call.

When Guardian is required, normal-transaction context documents remain unsupported and fail closed; separately proven auxiliary scope is required. Without Guardian enforcement, the existing context-document transaction path retains its metadata checks.

Transaction retention is separately opt-in with `transaction_documents_required=true` and `tx_retention_required=true`. At the default nine-worktree threshold ordinary checkout is refused; interrupted compactions continue reserving capacity until their SQL-projected commit. `tx-recycle-inspect`, `tx-recycle-submit` and `tx-recycle-drain` are the bounded recovery workflow. Historical TX lacking original binding are marked `SCOPE_UNRESOLVED`, never assigned to the current Task. Binding operating rules are indexed in `workshop/OPERATIONS.md#s-scratch-retention`.
