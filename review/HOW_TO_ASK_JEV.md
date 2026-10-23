+++
schema = "kairos-context/v1"
id = "WERKFADEN_HOW_TO_ASK_JEV"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/WERKFADEN_HOW_TO_ASK_JEV"
updated_at = "2026-10-23T23:47:06Z"
capsule = "Evidence discipline for atomic JEV propositions and separate sufficiency questions."
claim_boundary = "This method defines review discipline, not implemented gate coverage or measured probabilities."
entities = ["KAIROS", "WERKFADEN_HOW_TO_ASK_JEV"]
facets = ["jev", "ai-review", "limitations"]
criteria = []
does_not_answer = ["live project approval", "runtime correctness"]
[[answers]]
intent = "ai_review"
question = "Evidence discipline for atomic JEV propositions and separate sufficiency questions."
target = "s-overview"
+++

## CONTEXT INDEX

- `s-overview` — Review method and limits.

<a id="s-overview"></a>
# How to Ask JEV — Rules

## Purpose

JEV is not a general reasoning assistant and must not be treated as one.

Use JEV as a probabilistic evaluator of **small, explicit propositions against explicit evidence**.

For every important proposition, evaluate two separate questions:

1. **Proposition support**
   - Does the evidence currently shown support the proposition?

2. **Evidence sufficiency**
   - Is enough relevant evidence currently shown to make a reliable judgment about the proposition?

These questions MUST remain separate.

---

# 1. Never treat one JEV score as “truth”

Do not interpret:

```text
proposition = 0.92
```

as:

```text
The proposition is 92% true.
```

Interpret it as:

```text
Given the evidence currently shown, JEV assigns strong support to the proposition.
```

A proposition score is only meaningful together with evidence sufficiency.

Always reason about:

```text
J(P,E) = (
    proposition_support,
    evidence_sufficiency
)
```

Example:

```text
proposition_support = 0.92
evidence_sufficiency = 0.84
```

means:

```text
The visible evidence strongly supports the proposition,
and the evidence coverage is already substantial,
but the proposition is not yet equivalent to an established system-level fact.
```

---

# 2. Every important proposition gets two lanes

For every proposition `P`, ask JEV independently:

## Lane A — Proposition

```text
PROPOSITION:
<exact proposition>

EVIDENCE:
<current evidence>

QUESTION:
Does the supplied evidence support the proposition as literally stated?

TRUE:
The supplied evidence positively supports every material part of the proposition.

FALSE:
The supplied evidence materially contradicts the proposition.
```

This measures approximately:

```text
P(proposition supported | supplied evidence)
```

---

## Lane B — Evidence Sufficiency

Use the exact same proposition and the exact same evidence.

```text
PROPOSITION:
<exact same proposition>

EVIDENCE:
<exact same evidence>

QUESTION:
Does the supplied evidence contain enough relevant information to reliably
evaluate the proposition as a whole?

TRUE:
The evidence covers the material components, dependencies, transitions,
state, call paths, configuration, and behavior that could make the
proposition true or false.

FALSE:
One or more material dependencies required to evaluate the proposition
are not represented in the supplied evidence.
```

This measures approximately:

```text
P(evidence is sufficient to judge proposition)
```

Do not merge these questions.

---

# 3. Freeze the proposition

Once an investigation starts, do not silently change the proposition in response to JEV scores.

Bad:

```text
P0:
"The implementation is correctly wired end-to-end."

JEV = 0.58

P1:
"The main implementation path appears mostly correctly wired."
```

This invalidates the comparison.

Good:

```text
P:
"The committed processing profile is propagated unchanged from backend selection
through downstream execution and into the Worker handoff."
```

Keep `P` unchanged while evidence expands:

```text
E0
E1
E2
E3
...
```

Then compare:

```text
J(P,E0)
J(P,E1)
J(P,E2)
J(P,E3)
```

---

# 4. Do not fish for confirmation

Never search for additional evidence with the goal:

```text
Find evidence that supports P.
```

Never selectively add code because it is likely to increase the proposition score.

Instead ask:

```text
What relevant dependency is still missing from the evidence boundary?
```

or:

```text
What currently unseen code, state, configuration, caller, callee,
mutation point, runtime path, or authority could materially change
the truth value of P?
```

Evidence expansion must be driven by **coverage**, not by the desired conclusion.

The correct loop is:

```text
low evidence sufficiency
        ↓
identify missing evidence dimension
        ↓
retrieve that evidence neutrally
        ↓
re-evaluate the unchanged proposition
```

Not:

```text
low proposition score
        ↓
find supporting examples
        ↓
ask again
```

---

# 5. Evidence expansion must be falsification-oriented

When more context is needed, prefer evidence with the highest ability to falsify the current proposition.

Search for:

```text
alternative callers
alternative execution paths
mutation sites
legacy implementations
fallback paths
configuration overrides
registry bindings
runtime authority
production-only branches
error paths
state transitions
indirect dependencies
callers outside the visible module
code capable of bypassing the claimed invariant
```

Do not merely collect more examples of the expected behavior.

A good evidence request asks:

```text
Which unseen dependency could most strongly change the current assessment?
```

---

# 6. Never hide insufficient evidence behind a high proposition score

These states have different meanings.

```text
proposition = high
evidence = high
```

Meaning:

```text
Strongly supported with substantial evidence coverage.
```

---

```text
proposition = high
evidence = low
```

Meaning:

```text
Locally convincing, globally unproven.
Expand evidence.
```

Do NOT approve a system-level claim from this state.

---

```text
proposition = low
evidence = high
```

Meaning:

```text
Strong negative signal.
The available evidence is broad enough to make the disagreement meaningful.
Investigate for a real contradiction or defect.
```

---

```text
proposition = low
evidence = low
```

Meaning:

```text
Undetermined.
Do not conclude either truth or falsity.
Expand evidence.
```

---

# 7. Falling proposition score while evidence rises is valuable

Track score movement across evidence expansion.

Example:

```text
Run     Proposition     Evidence
E0      0.96            0.27
E1      0.91            0.49
E2      0.80            0.72
E3      0.53            0.94
```

This is not evaluator failure.

It means:

```text
As JEV gains enough evidence to judge the proposition,
the proposition becomes less defensible.
```

Treat this as a strong investigation signal.

In particular:

```text
Δ proposition < 0
while
Δ evidence > 0
```

may indicate that the local implementation looked correct while the broader system contains a contradictory path, dependency, state transition, or integration failure.

Use this signal to direct the next code investigation.

---

# 8. Make propositions atomic

Never ask JEV:

```text
Is this implementation correct, complete, safe, thread-safe,
production-ready, and properly integrated?
```

That contains several propositions.

Split it.

Example:

```text
P1:
The backend-selected processing profile reaches downstream execution unchanged.

P2:
Every production downstream execution execution invokes the Package A handoff.

P3:
No production Worker path can bypass that handoff.

P4:
Runtime authority resolves to the expected signed registry entry.

P5:
Parser or runner singleton state cannot alter the committed processing profile.
```

Evaluate each independently.

For every `Pi`, obtain:

```text
proposition_support(Pi)
evidence_sufficiency(Pi)
```

This creates a proposition graph instead of one vague overall judgment.

---

# 9. Do not mix missing evidence with contradiction

Whenever possible distinguish:

```text
SUPPORTED
CONTRADICTED
INSUFFICIENT_EVIDENCE
```

These are different states.

If a claim cannot be evaluated because important code is missing, do not label it false.

If visible code contradicts the claim and evidence coverage is high, do not label it merely uncertain.

Recommended result structure:

```text
proposition_support: <noul>

evidence_sufficiency: <noul>

relation:
    supported
    contradicted
    insufficient_evidence
```

If only two Nouls are available, preserve the same semantic distinction in downstream logic.

---

# 10. Keep JEV prompts neutral

Do not tell JEV what result is desired.

Bad:

```text
We suspect a serious backend bug.
Does this prove our concern?
```

Bad:

```text
This implementation should be correct.
Can you verify that?
```

Bad:

```text
The previous model found a likely race condition.
Do you agree?
```

Good:

```text
PROPOSITION:
...

EVIDENCE:
...

QUESTION:
Does the supplied evidence support the proposition as literally stated?
```

Avoid emotionally or directionally loaded wording.

---

# 11. Do not expose previous scores to the next evaluation

Unless the task explicitly concerns score history, do not place previous JEV outputs into the next JEV state.

Bad:

```text
Previous JEV result:
approval = 0.81
evidence = 0.63

Here is more code.
Re-evaluate.
```

Prefer:

```text
PROPOSITION:
<same proposition>

EVIDENCE:
<new complete evidence state>
```

The orchestration layer may compare runs.

JEV itself should normally evaluate the current state independently.

Desired relationship:

```text
J_n = f(P, E_n)
```

Avoid:

```text
J_n = f(P, E_n, J_(n-1))
```

This reduces anchoring and feedback effects.

---

# 12. Define evidence requirements explicitly

“Enough evidence” must not mean a vague feeling.

For code propositions, relevant evidence may include:

```text
definition
direct callers
material callees
value creation
value mutation
ownership
state transitions
alternative paths
fallback paths
configuration
registry bindings
authority resolution
error handling
production entry points
runtime observations
tests
build configuration
feature flags
persistence boundaries
thread boundaries
process boundaries
```

Only include categories materially relevant to the proposition.

Example:

```text
EVIDENCE REQUIREMENTS:

To evaluate this proposition, evidence should cover:

- where the processing profile is selected,
- where it is stored,
- every material mutation before downstream execution,
- downstream execution entry,
- the handoff call,
- alternate production paths,
- registry/authority resolution.
```

Then ask whether those requirements are sufficiently represented.

---

# 13. JEV does not replace evidence retrieval

JEV evaluates the state it sees.

It does not automatically know the entire repository.

Therefore:

```text
Werkfaden / retrieval / graph system
    =
find relevant evidence

JEV
    =
evaluate explicit propositions against that evidence
```

Do not infer:

```text
JEV did not see a contradiction
⇒
no contradiction exists in the repository
```

The correct interpretation is:

```text
JEV did not see a contradiction
within the supplied evidence boundary.
```

This is why the evidence-sufficiency lane is mandatory.

---

# 14. Codex must never silently fill missing code with assumptions

When evidence is incomplete:

Do not reason:

```text
This caller probably behaves like the others.
```

Do not reason:

```text
There is likely no other mutation site.
```

Do not reason:

```text
This appears to be the only production path.
```

Retrieve or prove it.

If it cannot currently be retrieved, lower the confidence in evidence sufficiency.

Missing evidence is an explicit state, not an invitation to infer.

---

# 15. Use JEV to test claims, not narratives

Prefer:

```text
"The selected profile reaches Worker::handoff unchanged."
```

over:

```text
"The Package A architecture is implemented correctly."
```

Prefer:

```text
"No production path invokes Worker::run without authority verification."
```

over:

```text
"The security architecture is sound."
```

Prefer machine-checkable or code-grounded propositions whenever possible.

---

# 16. Approval requires both lanes

Never approve a proposition using only proposition support.

Example policy:

```text
APPROVED only if:

proposition_support >= P_THRESHOLD
AND
evidence_sufficiency >= E_THRESHOLD
```

Thresholds are orchestration policy, not universal truth constants.

Example:

```text
P_THRESHOLD = 0.90
E_THRESHOLD = 0.90
```

Possible states:

```text
P >= threshold
E >= threshold
→ APPROVED
```

```text
P >= threshold
E < threshold
→ NEED_MORE_EVIDENCE
```

```text
P < threshold
E >= threshold
→ INVESTIGATE_CONTRADICTION
```

```text
P < threshold
E < threshold
→ UNRESOLVED
```

Do not collapse these into one score.

---

# 17. Do not average proposition and evidence scores

Never calculate:

```text
truth_score =
(proposition_support + evidence_sufficiency) / 2
```

Example:

```text
proposition = 0.99
evidence = 0.21
```

Average:

```text
0.60
```

This destroys the important meaning of the two dimensions.

Likewise:

```text
proposition = 0.23
evidence = 0.97
```

also averages to approximately `0.60`, despite representing a fundamentally different epistemic state.

Keep the dimensions separate.

---

# 18. Preserve the evidence history outside JEV

For every run record:

```text
proposition_id
proposition_text
evidence_set_id
evidence_sources
proposition_support
evidence_sufficiency
relation
timestamp/run id
```

This allows comparison:

```text
E0 → E1 → E2 → E3
```

and lets the system detect convergence or divergence.

The historical comparison belongs to the orchestration layer.

The current JEV query should remain clean.

---

# 19. Preferred investigation loop

Use this loop:

```text
1. Define an atomic proposition P.

2. Define the evidence requirements for P.

3. Retrieve an initial neutral evidence set E0.

4. Ask JEV:
   A = proposition support
   B = evidence sufficiency

5. Interpret A and B separately.

6. If B is insufficient:
   identify the most material missing evidence dimension.

7. Retrieve evidence for that missing dimension.
   Do not select evidence based on whether it supports P.

8. Construct E1.

9. Re-run the exact same proposition.

10. Compare:
    A0 → A1
    B0 → B1

11. Repeat until:
    - evidence is sufficient,
    - the proposition is strongly supported,
    - the proposition is strongly contradicted,
    - or no further material evidence can be obtained.

12. Only then make the orchestration decision.
```

---

# 20. Core principle

The system is not asking only:

```text
Does the code I see agree with this statement?
```

It is simultaneously asking:

```text
Do I see enough of the system to have the right to make that statement?
```

Those are separate epistemic questions.

JEV becomes useful as a truth-oriented measurement component only when both are preserved.

The correct mental model is:

```text
JEV does not measure absolute truth.

JEV measures the relationship between:

PROPOSITION
×
EVIDENCE
×
EVIDENCE COVERAGE
```

Therefore:

```text
high proposition + low evidence
!= verified

low proposition + low evidence
!= disproven

low proposition + high evidence
= meaningful negative signal

high proposition + high evidence
= meaningful positive signal
```

---

# 21. Check atomicity before sending

Apply this preflight before evaluating a proposition, not only after a surprising score.

Two promises belong in separate propositions when one can fail while the other remains true. A shared function, patch, or feature name does not make them atomic. Keyword counting is not a reliable atomicity check.

For example, upload rewind, preview-boundary detection, complete-header handling, and unchanged role selection are independently falsifiable. Do not ask for one approval of all four.

For every child proposition, record:

- a stable proposition ID, one exact behavioral assertion, and its input/scope boundary;
- the relevant evidence requirements and why the assertion is atomic;
- separate support and evidence-sufficiency questions using the same assertion and evidence.

The sender must review this record before transmission. If independent promises remain bundled, split the question and its evidence into bounded evaluations. Merely adding more evidence to a bundled question does not fix its scope.

Keep existing parent propositions and results unchanged. A decomposition creates explicitly linked child IDs; it must not silently weaken, replace, or relabel a failed parent. Parent readiness depends on every required child, combined in code, rather than an average score.

---

# 22. Separate source coverage, path coverage, and behavior checks

These are distinct evidence states:

```text
SOURCE_PRESENT: the relevant implementation was supplied.
PATH_REPRESENTED: the material branch and its activation conditions were identified.
BEHAVIOR_CHECKED: an authorized execution checked a stated expectation for a concrete input.
```

Supplying a fallback function does not mean its behavior was tested. Passing selected examples does not establish correctness for every input.

For each code proposition, keep a claim-to-evidence matrix. Each material row identifies:

- proposition ID and input precondition;
- main, alternative, fallback, or error path and the condition selecting it;
- source path, exact version/hash, and relevant lines, including material callers/callees;
- fixture identity or reproducible generator/hash, expected outcome, and observed outcome;
- check version, execution environment, result/receipt, and remaining limitations.

Use explicit check states such as `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED`, and `NOT_APPLICABLE`. Explain `NOT_APPLICABLE`. A model score must not turn `NOT_RUN` or an uncovered material path into `PASS`.

Derive relevant boundary cases from the implementation's branch conditions. For bounded parser previews, consider known versus unresolved delimiters, logical records versus physical lines, true EOF versus a preview cut, and role-inference fallbacks when no data rows were observed.

A concrete CSV regression seed is:

```python
raw = ('"time x\n' + 'a' * 9000 + '",v1,v2\n0,2,3\n1,4,5\n').encode('utf-8')
```

The complete file has three header fields. Its first field is quoted and contains a newline; the comma delimiter appears only after that field closes. An 8192-byte preview ends inside it. The preview must not publish separate `time` and `x` roles from that incomplete field. Keep controls for a complete ordinary header and for a delimiter visible before the quoted newline. This seed is a regression case, not an exhaustive parser proof.

Keep source-presence judgments separate from required behavioral checks in the approval record. A positive evidence-sufficiency Noul is not a measured test-coverage percentage. Required delivery checks that remain unexecuted or materially uncovered remain open, even when both JEV scores pass.

This rule grants no permission to compile, execute tests, Apply, or deploy. Existing project sequencing and authorization requirements still apply; perform checks only at their authorized stage.

---

# 23. Handle context overflow by decomposition first

Before sending, record the requested model, applicable provider context limits, and estimated request/per-question sizes. Distinguish estimates from token usage reported by the provider. Do not assume different tokenizers produce the same count.

When a request exceeds a limit or receives `max_tokens_exceeded`:

1. Record the rejection as not evaluated; it has no semantic approval result.
2. First try atomic child questions with only their material evidence. Preserve necessary dependencies and exact source identities. Do not remove contradictory branches or replace material code with reassuring summaries to fit a limit.
3. If decomposition cannot preserve a necessary evidence boundary within default limits, document why before using a supported long-context mode.

A long-context exception must record the exact request hash, model/version, mode/header, reason default-size packets were insufficient, affected propositions, and the provider's documented accuracy limitations. Retain required behavior checks; do not treat transport acceptance as a successful review or equivalent evaluation quality.

For a transport-only resubmission, retain the rejected attempt and show whether the request body is byte-identical. Record changed headers or transport settings separately. A request rejected before evaluation is different from repeating an evaluated request until a favorable score appears; the prohibition on score fishing remains unchanged.

Pin a model version when applying thresholds validated for that version. An alias or newer model is not evidence that a known failure has been fixed.

Provider references, checked 2026-10-22:

- Context limits, long-context tradeoffs, and aliases: https://docs.typesafe.ai/models
- Atomic Noul questions: https://docs.typesafe.ai/primitives/noul
- Jev 1.13 failure modes: https://docs.typesafe.ai/model-jaggedness/jev-1.13

Recheck version-specific limits and behavior before relying on them; do not treat this reference date as permanent provider authority.

---

# 24. Bind approval to its exact scope and required checks

`proposition_support` and `evidence_sufficiency` are application-defined questions. They are not built-in measurements of repository completeness, test coverage, or defect rate. Two positive model answers are not two independent proof procedures, and neither certifies zero escaped defects.

Before accepting a review result, record the exact child proposition IDs, evidence/source hashes, resolved model version, scores, thresholds, atomicity preflight, and claim-to-evidence matrix. Preserve the distinction between:

```text
JEV_APPROVED(P_i): both model scores meet policy for the recorded P_i and evidence.

DELIVERY_ELIGIBLE(scope): every required proposition is approved for that scope,
the reviewed bytes match the delivery candidate, required checks pass,
and the applicable governance and delivery gates are closed.
```

An unreviewed semantic change requires a new review identity. Do not transfer a positive score to broader behavior, another branch, changed bytes, or an untested system-level claim. Separately deliverable scopes need their own explicit dependency and approval boundary.

A verified material counterexample blocks the affected proposition despite an earlier positive score. Record the contradiction and reopen the affected scope; do not erase the earlier result or relabel a demonstrated contradiction as merely missing evidence. Re-evaluate only after a material repair or evidence change, preserving the exact history and anti-score-fishing rule.

These requirements describe review and delivery readiness. They do not automatically implement a validator, prove complete input coverage, or replace existing Workshop, scientific, runtime-proof, or release authority.

---

# Golden Rule

Never improve the answer.

Improve the evidence.

Keep the proposition fixed.

Let the score move where the evidence forces it to move.