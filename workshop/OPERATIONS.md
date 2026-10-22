+++
schema = "kairos-context/v1"
id = "KAIROS_WORKSHOP_OPERATIONS"
type = "documentation"
revision = 2
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_WORKSHOP_OPERATIONS"
updated_at = "2026-10-22T00:00:00Z"
capsule = "Operational sequence for jointly checking out, reviewing and applying source and affected Markdown, including scope expansion, heartbeat postcheck and conservative scratch retention."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_WORKSHOP_OPERATIONS"]
facets = ["workshop", "operations", "transaction", "semantic-context", "scratch-retention"]
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
+++
# Workshop operations

## CONTEXT INDEX

- [`s-overview`](#s-overview) — Checkout, edit, prepare, verify and apply source and its affected Markdown as one transaction.
- [`s-context-review`](#s-context-review) — Review semantic headers and sections before preparing the complete source and document package.
- [`s-scratch-retention`](#s-scratch-retention) — Keep active transactions and recovery evidence; bound disposable experiments without deleting governed knowledge.

<a id="s-overview"></a>
## Overview

> Capsule: Checkout, edit, prepare, verify and apply source and its affected Markdown as one transaction.

1. Bind and validate a project configuration.
2. Run `python workshop.py status`; resolve every issue at its owning authority.
3. Run `python workshop.py seal` to create the only checkout source.
4. Run `checkout --source <relative unit> --document <affected workspace-relative.md> --purpose "<bounded purpose>"`; repeat `--source` and `--document` as needed. The implementation Markdown is included automatically; extra documents are selected explicitly.
5. Edit only the returned `transactions/<id>/work/` files and complete both the source metadata review and every selected document review.
6. Run `prepare`, then `verify`, then the optional project parity checks.
7. Run `apply`; inspect the postcheck receipt and confirm the lease was released.

Mapping changes, membership migrations, and parity adapters require separate review. On a
refusal, stop, inspect state and hashes, and start a fresh bounded transaction after the
cause is resolved. Never hand-edit state, leases, seals, receipts, or the database.

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

For the source review, consider the implementation document's `capsule`, `answers`, `refs`,
`claim_boundary` and affected sections alongside the code. For each additional document, complete
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
the required revision/timestamp advances during preparation. Review sections and answer targets,
not only top-level frontmatter. Do not change historical references merely to match a new revision.

To add an affected existing document discovered during editing:

```text
python workshop/workshop.py --config <config> include-documents <transaction-id> --document docs/OTHER.md --reason "The patch also changes the consumer contract documented here"
```

This preserves current work, checks the live baseline, adds the document and resets the transaction
to `CHECKED_OUT`. Complete its review and rerun `prepare`, `verify` and any configured parity gate.
Every selected document must be reviewed, including selected documents that remain unchanged.
Generated authority and `code/*.md` documents cannot be added this way; use their source or
authority lifecycle. The ordinary context scope supports existing documents only.

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
