# JEV candidate binding: experimental Apply policy

This change adds a *conditional* (explicit `jev_apply_required: true`) JEV
admission check to the ordinary public `WorkshopEngine.apply` path. Existing
projects remain unchanged until this policy is deliberately enabled.

## Correct usage

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

## Limitations / not yet secure enough for production release authority

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

## Deferred, not implemented by this branch

The required persistent `transaction` document type, automatic heartbeat
projection on checkout/abort/apply, immutable recovery-safe candidate extraction,
disk retention accounting, ten-worktree quota, safe compaction and object-store
garbage collection are **not implemented** here. They must be delivered only after
the lifecycle/recovery and authority contracts are tested. Do not delete any
existing transaction data based on this experimental JEV admission.
