# Werkfaden Investigation Guardian

The Guardian is a deterministic pre-mutation gate for the normal Workshop `checkout` path.

It does **not** decide whether the model's reasoning is true. It verifies that the required
investigation sequence was traversed in order, that every state names the canonical Memory
passage re-checked for that state, that exact-source inspection is backed by a real verified
KAIROS `SIR_...` receipt, that the governed corpus and canonical Memory did not drift during
the investigation, and that the Workshop checkout scope exactly matches the source scope
frozen by `PROVE`.

## Guarded loop

```text
HUMAN PROBLEM
→ HYPOTHESIS
→ FALSIFIER 1
→ FALSIFIER 2
→ MAP
→ COUNTERPROBE
→ EXACT SOURCE
→ PROVE
→ PATCH
→ WORKSHOP
→ HEARTBEAT
→ FRESH RUN
```

`PATCH` cannot be advanced manually. A successful guarded Workshop `checkout` is the only
operation that moves a Guardian session from `PROVE` to `PATCH`.

## Enable the gate

Set this in the local `runtime-sync-workshop-config/v1` file:

```json
{
  "guardian_required": true
}
```

This is fail-closed for the normal source-edit path. The environment variable
`WERKFADEN_GUARDIAN_REQUIRED=1` can force the gate on for a temporary test, but an
environment variable cannot turn off a config-level requirement.

Guardian v1 deliberately blocks `source-set-checkout` and `authority-checkout` while the
gate is required. Those mutation classes need their own proven-scope contract instead of
silently bypassing the normal source guardian.

## Start one investigation

The Human supplies the exact canonical Memory path. The agent must not guess it.

```powershell
python workshop.py guard-start `
  --problem "Describe the Human-observed problem without claiming a root cause." `
  --memory-file "D:\...\MEMORY.md" `
  --memory-ref "MEM.LOOP"
```

The command returns a `GRD_...` identifier and binds:

- the current verified Workshop package hash;
- the exact canonical Memory path and SHA-256;
- the Human problem;
- the first Memory reference.

Any Memory or governed-package drift before PATCH invalidates the investigation and forces
a restart from `HUMAN PROBLEM`.

## Advance states

Each state requires at least one `--memory-ref`. The reference is the relevant passage/code
the agent re-verified immediately before executing that state.

Example:

```powershell
python workshop.py guard-step GRD_... `
  --step HYPOTHESIS `
  --summary "One explicit falsifiable mechanism now explains the Human observation." `
  --memory-ref "MEM.RULE.NO_GUESS" `
  --memory-ref "MEM.RULE.LIVE_AUTH"
```

Evidence-bearing states also require `--evidence` handles.

```powershell
python workshop.py guard-step GRD_... `
  --step FALSIFIER_1 `
  --summary "Semantic authority supports the same bounded hypothesis." `
  --memory-ref "MEM.LOOP" `
  --evidence "SRR_..."
```

`EXACT_SOURCE` is stronger: at least one evidence reference must be a real verified
KAIROS source-inspection receipt:

```powershell
python workshop.py guard-step GRD_... `
  --step EXACT_SOURCE `
  --summary "The predicted exact source surface was inspected through KAIROS." `
  --memory-ref "MEM.RET.SOURCE" `
  --evidence "SIR_..."
```

`PROVE` freezes the only sources that may enter Workshop coding:

```powershell
python workshop.py guard-step GRD_... `
  --step PROVE `
  --summary "Observed defect, violated invariant, causal owner and affected scope are evidenced." `
  --memory-ref "MEM.RULE.GENERAL_FIX" `
  --evidence "proof:causal-chain" `
  --source "runtime/example.cpp"
```

## Coding gate

When `guardian_required=true`, ordinary `checkout` without a Guardian is rejected.

The requested `--source` list must exactly equal the scope frozen by `PROVE`.

```powershell
python workshop.py checkout `
  --guardian GRD_... `
  --guardian-memory-ref "MEM.RULE.GENERAL_FIX" `
  --guardian-memory-ref "MEM.WORKSHOP" `
  --source "runtime/example.cpp" `
  --purpose "Repair the proven general mechanism only."
```

A successful checkout binds the `TXN_...` transaction into the Guardian ledger and advances
the state to `PATCH`.

This means the normal model-visible coding directory is not created until the pre-patch
chain is complete:

```text
HYPOTHESIS
→ FALSIFIER_1
→ FALSIFIER_2
→ MAP
→ COUNTERPROBE
→ EXACT_SOURCE
→ PROVE
→ guarded checkout
→ PATCH
```

## Closing the loop

After PATCH, the remaining states are recorded with `guard-step`:

```text
WORKSHOP
→ HEARTBEAT
→ FRESH_RUN
```

`WORKSHOP` must reference the exact transaction bound at checkout. `FRESH_RUN` closes the
Guardian session. The next observed problem starts a new `guard-start`; the prior hypothesis
does not become the default explanation for the next run.

## Security boundary

The Guardian is deterministic workflow enforcement, not a hostile-process sandbox.

It can prevent the supported Workshop checkout path from opening a coding transaction
before the required chain exists. It cannot stop a process that already has unrestricted OS
write access from bypassing Workshop entirely. That stronger boundary belongs to ACLs and/or
a later shell hook.

The useful split is:

```text
Guardian = cannot enter the supported coding path before PROVE
Hook/ACL = cannot bypass the supported coding path
```
