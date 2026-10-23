+++
schema = "kairos-context/v1"
id = "KAIROS_WORKSHOP_OPERATIONS"
type = "documentation"
revision = 4
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_WORKSHOP_OPERATIONS"
updated_at = "2026-10-23T23:52:16Z"
capsule = "Operational sequence for ordinary Workshop patches: update source and semantic headers, complete deterministic metadata spot checks, obtain bound clearance, verify and apply before heartbeat postcheck."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_WORKSHOP_OPERATIONS"]
facets = ["ai-review", "guardian", "workshop", "operations", "transaction", "semantic-context", "metadata-aftercheck", "scratch-retention"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "workshop"
question = "What is the operational sequence for a Workshop transaction?"
target = "s-overview"

[[answers]]
intent = "context_review"
question = "How do I include and review affected Markdown in a Workshop transaction?"
target = "s-context-review"

[[answers]]
intent = "scratch_retention"
question = "Which Workshop scratch artifacts must be retained?"
target = "s-scratch-retention"

[[search_contract]]
query = "What is the operational sequence for a Workshop transaction?"
expected = "KAIROS_WORKSHOP_OPERATIONS#s-overview"
required_top_k = 1
[[answers]]
intent = "ai_review"
question = "Does Workshop enforce JEV AI approval before Apply?"
target = "s-standalone-ai-review"

+++
# Workshop operations

## CONTEXT INDEX

- `s-standalone-ai-review` â€” Standalone JEV submission, combined Guardian/metadata checks and missing AI enforcement.

- [`s-overview`](#s-overview) â€” Update source and semantic metadata, complete the aftercheck, then verify and apply the bound package.
- [`s-context-review`](#s-context-review) â€” Review semantic headers and sections before preparing the complete source and document package.
- [`s-scratch-retention`](#s-scratch-retention) â€” Keep active transactions and recovery evidence; bound disposable experiments without deleting governed knowledge.

<a id="s-overview"></a>
## Overview

> Capsule: Update source and semantic metadata, complete the aftercheck, then verify and apply the bound package.

1. Bind and validate a project configuration.
2. Run `python workshop.py status`; resolve every issue at its owning authority.
3. Run `python workshop.py seal` to create the only checkout source.
4. Run `checkout --source <relative unit> --document <affected workspace-relative.md> --purpose "<bounded purpose>"`; repeat `--source` and `--document` as needed. The implementation Markdown is included automatically; extra documents are selected explicitly.
5. Edit only the returned `transactions/<id>/work/` files. For every changed source/header, update its semantic metadata header; complete the source review and every selected document review.
6. Run `metadata-plan <transaction-id>`. Read the selected source/document evidence and complete the pending field decisions and spot checks in `review/metadata_audit.json`.
7. Run `prepare <transaction-id>`, then `metadata-check <transaction-id>`, then `verify <transaction-id>`. A required metadata clearance must exist before verification or apply.
8. Run `apply <transaction-id>`; inspect the postcheck receipt and confirm the lease was released.

These metadata-plan/check steps apply to ordinary transactions with changed code bytes. Pure
Markdown changes retain their existing document-review requirements. Project-specific parity
checks, where implemented, are a separate obligation; metadata clearance is not a build/test result.

Mapping changes, membership migrations, and parity adapters require separate review. On a
refusal, stop, inspect state and hashes, and start a fresh bounded transaction after the
cause is resolved. Never hand-edit state, leases, seals, receipts, or the database.

For an authority migration that changes existing source/header bytes, update the corresponding
semantic headers, run `metadata-plan`, and complete the audit before `authority-prepare`. Then
run `metadata-check`, `authority-verify` and `authority-apply`. Newly created and removed files
keep the migration's creation/removal review; no previous header is invented for a new file.

Keep existing source bytes unchanged during a static source-set migration. If
`source-set-prepare` reports `ORDINARY_METADATA_PATCH_REQUIRED`, move the listed existing-source
edits to an ordinary transaction with the metadata workflow above; perform the membership
migration separately. New, removed and renamed source paths retain their source-set lifecycle.

<a id="s-context-review"></a>
## Context review

> Capsule: Review semantic headers and sections before preparing the complete source and document package.

Use `--config <project-workshop-config>` before the command when invoking the unbound package.
For example, from the package root:

```text
python workshop/workshop.py --config <config> checkout --source app.py --document docs/API.md --purpose "Update behavior and its documented API contract"
```

The checkout returns the transaction identifier, `work_directory`, `metadata_review` and
`document_review`. The source and its implementation Markdown are under `work/` and
`work/blueprints/`; additional documents are under `work/documents/`, preserving their workspace
paths. Edit the copies rather than the live project or workspace.

For the source review, check the implementation document's `capsule`, `answers`, `refs`,
`claim_boundary`, `entities`, `facets`, `does_not_answer`, `search_contract` and affected sections
against the candidate code. At least one actual value in those eight header fields must change
for each changed source/header; updating only a body sentence, revision or timestamp is insufficient.
Every field also needs its own audit decision and concrete explanation, including correct values
left unchanged. This is required for Python/TypeScript/Rust and other admitted languages as well
as C-family patches. For each additional document, complete
its entry in `review/context_review.json`:

```json
{
  "document": "docs/API.md",
  "metadata_impact": "updated",
  "reason": "The new return behavior changes the documented API answer and claim boundary.",
  "reviewer": "<reviewer identity>",
  "reviewed_at": "<UTC timestamp>",
  "reviewed_fields": ["capsule", "answers", "refs", "claim_boundary", "sections"]
}
```

Use `updated` when semantic content changed. Use `none` only after checking all five fields and
finding the current claims remain accurate; the document's semantic content must then remain
unchanged. Changing only a revision or timestamp cannot satisfy `updated`. The Workshop generates
the required revision/timestamp advances during preparation. This `none` decision concerns an
additional document and does not waive the changed source's semantic-header obligation. Review
sections and answer targets, not only top-level frontmatter. Do not change historical references
merely to match a new revision.

The required aftercheck uses the generated template rather than a hand-made PASS receipt:

```text
python workshop/workshop.py --config <config> metadata-plan <transaction-id>
# Read the selected source/document evidence and complete review/metadata_audit.json.
python workshop/workshop.py --config <config> prepare <transaction-id>
python workshop/workshop.py --config <config> metadata-check <transaction-id>
python workshop/workshop.py --config <config> verify <transaction-id>
python workshop/workshop.py --config <config> apply <transaction-id>
```

Fill the generated audit in place. This fragment shows the field-review and metadata-evidence
shape; replace placeholders with the plan's real sample ID, an exact candidate quotation and
your concrete findings. Keep every generated field and sample, including source quotes and ranges.

```json
{
  "field_reviews": {
    "capsule": {"action": "updated", "reason": "Explain how the changed capsule matches the candidate.", "sample_ids": ["<generated sample ID>"]},
    "claim_boundary": {"action": "unchanged", "reason": "Explain why the existing boundary still holds.", "sample_ids": ["<generated sample ID>"]}
  },
  "samples": [{
    "id": "<generated sample ID>",
    "verdict": "pass",
    "reason": "Explain what the selected source lines establish for this claim.",
    "metadata_evidence": [{"field": "capsule", "quote": "<exact quotation from the candidate capsule>"}]
  }]
}
```

Use `updated` only when that field's actual value changed, otherwise `unchanged`. Each field
needs a reason and at least one valid sample ID. A quoted field must link back to that sample.

Use the plan's exact sample locations and revision anchors. Explain the claim being checked,
what the cited source establishes, and why the changed or retained header value is accurate.
Provide a real reviewer label and an RFC-3339 timestamp. A missing, partial or failed check blocks
progress: correct the documentation, refresh stale plan evidence, and review it again. The gate
checks the evidence bindings and declared work; the agent remains responsible for the meaning.
Never mark unsupported relationships or broad claims as proved just to complete a sample.

To add an affected existing document discovered during editing:

```text
python workshop/workshop.py --config <config> include-documents <transaction-id> --document docs/OTHER.md --reason "The patch also changes the consumer contract documented here"
```

This preserves current work, checks the live baseline, adds the document and resets the transaction
to `CHECKED_OUT`. Complete its review, refresh the plan and audit for a code patch, and rerun
`prepare`, `metadata-check`, `verify` and any separately implemented parity gate.
Every selected document must be reviewed, including selected documents that remain unchanged.
Generated authority and `code/*.md` documents cannot be added this way; use their source or
authority lifecycle. The ordinary context scope supports existing documents only.

Any source, Markdown, scope or review change after metadata clearance requires fresh preparation,
metadata checking and verification. Clearance is tied to the reviewed candidate; do not carry it
over to a different patch. The required metadata work must finish before apply, never afterwards.

After `apply`, require `POSTCHECK_VERIFIED` and `bit_exact=true`. Its heartbeat must have promoted
the changed Markdown before success is reported. If apply enters `RECOVERY_REQUIRED`, preserve
the work and rollback directories and follow recovery. A semantic correction found after a
successful apply starts a new bounded transaction.

<a id="s-scratch-retention"></a>
## Scratch retention

> Capsule: Keep active transactions and recovery evidence; bound disposable experiments without deleting governed knowledge.

Keep experiments in an identified temporary directory and remove disposable test outputs when
their evidence has been recorded. Reuse a transaction's work tree instead of accumulating unnamed
copies. This is an operating policy; the Workshop does not yet provide automatic transaction pruning.

Never delete an active, leased or recovery-required transaction, a current seal's content-addressed
objects, or work/baseline/rollback bytes still required by recovery. Retain terminal receipts and
the evidence needed to explain an applied change. Any later pruning must enumerate disposable
paths, confirm their terminal state and preserve referenced evidence before deleting them.
Configured dependent tests and governed Markdown are source artifacts, not disposable Workshop
outputs. Scratch cleanup does not erase knowledge from the KAIROS database.

<a id="s-standalone-ai-review"></a>
## Standalone AI review and combined branch

> Capsule: The standalone JEV port submits actual changed code but does not authorize or gate Workshop Apply.

The publication combines Guardian v2 with the metadata-aware Workshop core. Existing deterministic gates retain their own scope. Optional JEV submission, separate evidence-sufficiency questions and reconsideration tools live under `review/`; see [the operating instructions](../review/README.md) and [the comparison](../review/COMPARISON.md). No supported Apply path consumes a JEV approval. A metadata reviewer field or Guardian receipt is not evidence of an AI call.

When Guardian is required, normal-transaction context documents remain unsupported and fail closed; separately proven auxiliary scope is required. Without Guardian enforcement, the existing context-document transaction path retains its metadata checks.
