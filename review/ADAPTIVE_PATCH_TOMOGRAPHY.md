+++
schema = "kairos-context/v1"
id = "WERKFADEN_JEV_TOMOGRAPHY"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/WERKFADEN_JEV_TOMOGRAPHY"
updated_at = "2026-10-23T23:47:06Z"
capsule = "Evidence discipline for atomic JEV propositions and separate sufficiency questions."
claim_boundary = "This method defines review discipline, not implemented gate coverage or measured probabilities."
entities = ["KAIROS", "WERKFADEN_JEV_TOMOGRAPHY"]
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
# Jev Adaptive Patch Tomography

## Purpose

This contract defines a reusable method for extracting high-resolution patch guidance from Jev even though Jev returns only typed yes/no probabilities and does not generate explanations.

The method treats Jev as a **binary semantic instrument**, not as a text reasoner.

The central question is always:

```text
Is this candidate code correct for this goal under the current evidenced architecture?
```

The answer to that question is intentionally not the final result. It is the root of a recursively expanded question tree.

A weak or negative answer is decomposed into smaller, evidence-backed yes/no propositions until the remaining open branches point at exact contracts, relations, owners, files, or changed code regions.

The output is a **diagnostic tree of supported and unsupported propositions** that the main coding LLM can use for the next reasoning or patch step.

\---

# 1\. Non-negotiable roles

## Main coding LLM / Codex

The main LLM remains the active investigator and code author.

It:

* understands the Human task;
* navigates KAIROS/Werkfaden;
* forms hypotheses;
* creates the Workshop candidate patch;
* reasons over the final Jev tree;
* decides what additional evidence to acquire or what candidate code to change.

It does **not** get to treat its own selected evidence as sufficient merely because it selected it.

## KAIROS / Werkfaden

KAIROS/Werkfaden is the passive, queryable project archive and evidence/index layer.

It supplies:

* task and goal authority;
* architecture sections;
* graph relations;
* graph contracts;
* artifacts and provenance bindings;
* evidence anchors;
* current implementation/document routes;
* current SQL/graph/search results.

Search/graph/SQLite locate evidence. They do not replace the source that owns a claim.

## Workshop

Workshop supplies the real candidate mutation boundary.

For a normal existing-file patch:

```text
sealed live code                  = BEFORE
transactions/TXN\\\_<id>/work/...   = CANDIDATE AFTER
```

The candidate patch is therefore measured from real bytes, not from an agent-authored patch description.

## Jev

Jev answers only narrow propositions about supplied state.

Jev does not:

* retrieve the project by itself;
* invent missing architecture;
* explain its answer in free text;
* select project authority;
* authorize mutation;
* prove post-run success before the run exists.

\---

# 2\. Score semantics

Every tomography question MUST be phrased so that **YES is the desired/satisfied state**.

Examples:

```text
GOOD:
Does the candidate preserve the required fail-closed behavior?

GOOD:
Does the candidate avoid introducing a dataset-specific fallback?

BAD:
Does the candidate contain an unsupported fallback?
```

This makes every node use the same interpretation.

Default workflow policy uses a **dual gate for every substantive proposition**:

```text
proposition\\\_yes >= 0.95
AND
evidence\\\_sufficient\\\_for\\\_this\\\_proposition >= 0.95
    -> PASS

otherwise
    -> EXPAND
```

This prevents a confident-looking YES from closing a branch when the supplied state is too thin to justify that judgment.

`0.95` is a workflow threshold, not an intrinsic law of Jev and not a statement that the patch is "95% correct".

A score below the threshold means only:

```text
this proposition is not yet supported strongly enough to close this branch
```

It does NOT mean:

```text
0.36 = 36% of the implementation is complete
0.31 = 31% of three bindings are implemented
```

Never encode counts or partial completion into Noul probabilities.

If three things must be checked, ask three questions.

\---

# 3\. Level 0 — the universal patch matrix

Every patch attempt begins with the same high-level question matrix.

Each question receives the concrete Workshop BEFORE/AFTER code, the task/transaction purpose, current architecture evidence, and relevant KAIROS graph/search context.

Recommended Level-0 roots:

```text
L0.01 GOAL ALIGNMENT
Does the candidate directly advance the supplied task/transaction goal?

L0.02 ARCHITECTURE CONSISTENCY
Is the candidate consistent with the supplied current architecture authorities and contracts?

L0.03 OWNING MECHANISM
Does the candidate modify the abstraction/mechanism that owns the evidenced problem rather than only the visible symptom?

L0.04 CAUSAL REPAIR
Does the candidate address the evidenced causal failure mechanism?

L0.05 SCOPE COMPLETENESS
Does the candidate include every implementation owner that the supplied evidence requires for this repair?

L0.06 SCOPE BOUNDEDNESS
Is the candidate limited to behavior justified by the supplied task and evidence?

L0.07 UPSTREAM CONTRACTS
Does the candidate preserve all applicable upstream producer/input contracts represented in the supplied evidence?

L0.08 DOWNSTREAM CONTRACTS
Does the candidate preserve all applicable downstream consumer/output contracts represented in the supplied evidence?

L0.09 STATE / IDENTITY PROPAGATION
Does the candidate correctly propagate every required state, identity, capability, or binding represented in the supplied evidence?

L0.10 ARTIFACT / PROVENANCE BINDING
Does the candidate preserve required artifact, hash, provenance, and evidence bindings represented in the supplied evidence?

L0.11 VALIDATION / GATING SEMANTICS
Does the candidate preserve or correctly implement all applicable validation, admission, and gate semantics represented in the supplied evidence?

L0.12 FAIL-CLOSED SEMANTICS
Does the candidate preserve required fail-closed behavior when authority, calibration, state, or evidence is missing or invalid?

L0.13 GENERAL SOLUTION
Does the candidate repair the general mechanism rather than only the observed dataset, lane, profile, fixture, test, or customer case?

L0.14 RUNTIME AGNOSTICISM
Is the candidate behavior derived from governed inputs/contracts/state instead of hard-coded scenario or environment identity?

L0.15 NO FALLBACK / BYPASS
Does the candidate avoid introducing a fallback, default semantic substitution, bypass, silent downgrade, or reduced-scenario success path?

L0.16 NO RESIDUAL PATH
Does the candidate remove the same failure mechanism from every supplied reachable path rather than leaving an alternate path that still violates the invariant?

L0.17 SCIENTIFIC / SEMANTIC FIDELITY
Does the candidate preserve all applicable scientific semantics, calibration meaning, determinism, and claim boundaries represented in the supplied evidence?

L0.18 PERSISTENCE / RECOVERY COHERENCE
Does the candidate remain coherent with applicable persistence, state-transition, recovery, and replay semantics represented in the supplied evidence?

L0.19 INTERNAL PATCH COHERENCE
Are the concrete candidate edits mutually coherent and non-contradictory?

L0.20 ATTEMPT EVIDENCE SUFFICIENCY
Is there enough supplied evidence to justify compiling/testing this exact candidate without filling missing project facts by assumption?
```

These are root diagnostics, not the final explanation.

Every root whose proposition OR evidence-sufficiency gate is below threshold enters the expansion loop.

\---

# 4\. Core expansion loop

For every failed/open node `N`:

```text
N proposition score < threshold
OR N evidence-sufficiency score < threshold
        |
        v
1. Query KAIROS with the proposition itself
        |
        v
2. Retrieve bounded current evidence
        |
        v
3. Extract structured evidence candidates
        |
        v
4. Ask Jev which evidence candidates are materially relevant to N
        |
        v
5. Re-ask N with the new relevant evidence
        |
        +--> PASS -> close branch
        |
        v
6. Convert relevant evidence into smaller atomic child propositions
        |
        v
7. Ask all children in parallel
        |
        v
8. PASS children close; failed children recursively repeat this loop
```

This distinction is mandatory:

```text
same question + same evidence again       = forbidden retry
same question + materially new evidence   = valid re-check
```

\---

# 5\. KAIROS query generated from a failed Jev proposition

The failed proposition itself becomes the next retrieval question.

Generic query template:

```text
Which current project contracts, architecture relations, owners, artifacts,
evidence anchors, or implementation authorities determine whether the following
candidate-patch proposition is true for the active task?

<FAILED PROPOSITION>
```

Use `breadth` because an unresolved patch proposition may have competing owners, contradictory branches, or omitted dependencies.

Recommended bounded call:

```text
kairos search
  --mode breadth
  --limit 30
  --max-hops 2
  <generated question>
```

`30` is a workflow budget, not authority. It may be configured.

The script must also use structured graph routes when the retrieved metadata gives exact identities:

```text
partial/bare identity  -> graph --resolve
exact entity           -> graph --node
2+ relation hops       -> graph --chase --max-hops 2
asset identity          -> graph --asset
```

Never treat search rank as evidence strength.

\---

# 6\. Evidence-candidate filtering

A KAIROS search may return up to dozens of sections/relations/contracts.

Do not blindly turn all of them into patch obligations.

First ask Jev a materiality question for each candidate evidence item:

```text
Is evidence item E materially relevant to deciding parent proposition N?
```

Only evidence items that pass the configured materiality threshold are admitted into the branch.

This creates an important separation:

```text
KAIROS retrieves candidates.
Jev judges semantic relevance to the exact proposition.
The script controls the recursion.
```

\---

# 7\. Deterministic child generation

The script MUST NOT ask a generative LLM to invent the next Jev questions.

Child questions are generated from the **type of retrieved evidence** with fixed templates.

## Contract row

Given:

```text
kind = invariant
subject = X
statement = S
```

Generate:

```text
Does the candidate satisfy this current project contract for X: S?
```

## Relation row

Given:

```text
A --predicate--> B
```

Generate:

```text
Is the candidate consistent with the required current relation
A --predicate--> B?
```

## Artifact row

Given:

```text
artifact = A
operation = OP
producer\\\_or\\\_consumer = X
```

Generate:

```text
Does the candidate preserve or correctly implement X's required OP relationship
with artifact A?
```

## Unresolved drift row

Generate:

```text
Does the candidate avoid preserving or reintroducing this unresolved drift:
<summary>?
```

## Evidence clause / section

When no normalized graph row exists but KAIROS returned a bounded evidence section,
extract bounded clauses deterministically and ask:

```text
Does the candidate satisfy the requirement/constraint expressed by this exact
evidence clause: <clause>?
```

Do not synthesize a new architectural claim from prose.

\---

# 8\. Patch localization children

Architecture obligations tell the agent **what** is wrong.

Patch localization tells it **where the candidate is wrong**.

For a failed parent proposition, the script may generate file-level children for actual changed Workshop files:

```text
Is the actual candidate change in <FILE> correct and necessary for satisfying <PARENT>?
```

A failed file child expands to its actual mechanically detected changed regions:

```text
Does the candidate change in <FILE>, candidate lines <A-B>, correctly satisfy <PARENT>?
```

No manually supplied line list is permitted.

The line/region set must come from the real sealed-live vs Workshop-work comparison.

\---

# 9\. Four-way diagnostic refinement for a failed atomic obligation

When an atomic architecture/contract obligation remains below threshold, refine it with four separate propositions.

For obligation `O`:

```text
O.before
Does the sealed BEFORE implementation satisfy O?

O.after
Does the CANDIDATE AFTER implementation satisfy O?

O.delta
Does the concrete candidate change directly repair the BEFORE violation of O?

O.complete
Does the CANDIDATE eliminate the supplied reachable violation of O without leaving a residual path?
```

Interpret the pattern, not a fake percentage of completion.

Example:

```text
before   0.08
before?  -> likely violation existed

after    0.21
candidate still not supported

delta    0.29
edit probably does not repair the mechanism

complete 0.09
residual violation very likely remains
```

The script may summarize this pattern for the coding LLM, but must preserve all raw scores.

\---

# 10\. Owner localization

If an obligation remains open and KAIROS supplies several possible owners, ask one proposition per owner:

```text
Does satisfying O require an additional or different change in owner A?
Does satisfying O require an additional or different change in owner B?
Does satisfying O require an additional or different change in owner C?
```

If the owner is not part of the candidate patch, that does not authorize editing it.

It means:

```text
inspect / verify this owner before claiming the patch scope is complete
```

The main LLM must still follow normal KAIROS source-authority and Workshop rules.

\---

# 11\. Optional optimization for large candidate sets: binary group fan-out

When one branch has too many candidate owners/evidence rows to ask individually within the configured resource budget, it MAY group them deterministically. The reference script currently prefers direct batching up to its configured child/node limits; group fan-out is an optional later optimization.

Example with 40 owners:

```text
G1 = owners 1..20
G2 = owners 21..40
```

Ask both:

```text
Does satisfying O require additional inspection/change in at least one owner in G1?
Does satisfying O require additional inspection/change in at least one owner in G2?
```

Every positive/open group is split again.

Important:

* groups are NOT mutually exclusive;
* both branches may be positive;
* this is a fan-out tree, not an exclusive binary search;
* once a group is small enough, return to one-question-per-owner.

\---

# 12\. Tree node schema

Every question becomes a persistent diagnostic node.

Recommended schema:

```json
{
  "node\\\_id": "N000123",
  "parent\\\_id": "N000041",
  "depth": 3,
  "kind": "contract|relation|artifact|file|hunk|root|diagnostic",
  "proposition": "Does ... ?",
  "score": 0.42,
  "evidence\\\_score": 0.88,
  "threshold": 0.95,
  "status": "PASS|EXPAND|PASS\\\_AFTER\\\_RETRIEVAL|STALLED|LIMIT\\\_REACHED",
  "evidence\\\_digest": "...",
  "retrieval\\\_query": "...",
  "evidence\\\_refs": \\\[],
  "metadata": {},
  "children": \\\[]
}
```

Never collapse the tree to one average score.

Averages destroy the diagnostic information.

\---

# 13\. Loop / duplication protection

The recursive system MUST prevent confidence fishing.

Hash at least:

```text
normalized proposition
+ canonical evidence payload
+ candidate patch identity
```

If the same proposition with the same evidence digest has already been asked:

```text
DO NOT CALL JEV AGAIN
```

A re-check is valid only after new evidence changes the digest.

Also deduplicate generated child propositions globally.

\---

# 14\. Stop rules

A branch stops when any of these becomes true:

## PASS

```text
proposition\\\_yes >= threshold
AND
evidence\\\_sufficient\\\_for\\\_this\\\_proposition >= threshold
```

## No new evidence

KAIROS returns no materially new evidence and the same evidence digest would be reused.

Result:

```text
STALLED / NOT\\\_DERIVABLE\\\_FROM\\\_CURRENT\\\_EVIDENCE
```

Do not guess.

## Atomic code leaf reached

The branch is already bound to:

```text
one exact contract/relation
+ one exact owner/file
+ one exact changed region
```

and additional retrieval yields nothing new.

This is a useful unresolved leaf for Codex.

## Resource guard

Stop on configured:

```text
max depth
max nodes
max KAIROS queries
max Jev calls
max state size
```

The result must say `LIMIT\\\_REACHED`, never `PASS`.

\---

# 15\. Output for the main coding LLM

The primary output is not a wall of probabilities.

Produce two artifacts:

```text
jev\\\_patch\\\_tree.json      complete machine-readable tree
jev\\\_patch\\\_tree.md        compact coding-LLM diagnostic report
```

The Markdown report should prioritize unresolved leaves.

When the attempt gate has already returned a low review, invoke the tree with
that review as `--focus-review`. The tree then replaces the generic cold-start
roots with the failed Jev gate dimensions, preserves each score and original
criterion, and turns each one into a targeted KAIROS search/retrieval query.
Retrieved exact identities may trigger bounded graph resolution; unresolved or
graph-only routes remain explicitly marked and do not become source truth.
This is the required feedback path from Jev weakness to KAIROS evidence.

Example:

```text
## OPEN PATCH OBLIGATIONS

### O17 — scientific execution binding
Path:
L0.09 STATE/IDENTITY PROPAGATION
-> contract C17
-> scientific\\\_execution\\\_binding\\\_v2
-> candidate region 812-846

Scores:
- after satisfies obligation: 0.21
- delta repairs violation: 0.29
- residual violation eliminated: 0.09

Evidence:
- CONTRACT ...
- RELATION ...
- artifact#section ...

Interpretation boundary:
The candidate is not sufficiently supported for this exact obligation.
This does not itself specify the replacement code.
```

Codex/Sol then reasons over this tree and creates the next candidate.

\---

# 16\. The essential feedback loop

The complete development loop becomes:

```text
Human problem
    |
    v
Codex investigation
    |
    v
Workshop candidate
    |
    v
Jev Level-0 tomography
    |
    +---- passing roots -----------------------+
    |
    v
failed/open root
    |
    v
KAIROS query using failed proposition
    |
    v
contracts / relations / artifacts / evidence anchors
    |
    v
Jev materiality filter
    |
    v
parent re-check with new evidence
    |
    +---- pass -> close branch
    |
    v
atomic child questions
    |
    v
recursive evidence tree
    |
    v
exact unresolved leaves
    |
    v
Codex reasons over leaves and revises candidate
    |
    v
new Workshop candidate bytes
    |
    v
fresh tomography tree
    |
    v
compile / test / fresh run only when patch-attempt gate is sufficiently supported
```

Post-run closure remains a separate evidence problem.

The controller enforces this loop at the Workshop boundary. A low attempt
terminates the current invocation with a revision handoff and a candidate-byte
fingerprint. The next Jev call must reference that receipt and must observe a
changed reviewable candidate surface; an unchanged candidate is returned as
`WAITING\\\_FOR\\\_CANDIDATE\\\_REVISION` without another Jev request. The controller
therefore does not silently turn tomography into implementation or confidence
fishing: Codex must reason over the atomic leaves, revise the transaction work
tree, and submit the fresh bytes for a new gate.

\---

# 17\. Hard prohibitions

Never:

* interpret Noul probability as percentage completion;
* ask one Noul to encode a count (`0.33 = one of three`);
* repeat identical questions against identical evidence to obtain a nicer number;
* let the coding LLM exclusively curate the evidence Jev sees;
* treat search score as truth;
* treat a graph row without its authority/evidence boundary as universal truth;
* infer a missing architecture edge;
* turn `score < 0.95` directly into "code is wrong" before retrieval/re-check;
* require post-fix run artifacts at the pre-execution patch-attempt stage;
* average the tree into one confidence number;
* allow a passing Jev branch to bypass Workshop, KAIROS, Human, test, scientific, or release authority.

\---

# 18\. Core principle

```text
Jev does not explain the patch.

The system explains the patch by asking Jev enough small questions that
only a small number of exact propositions remain unsupported.
```

The desired end state is not:

```text
"Jev says 0.34, therefore patch bad."
```

It is:

```text
Thousands of possible assumptions
        |
        v
bounded evidence retrieval
        |
        v
atomic yes/no propositions
        |
        v
supported branches removed
        |
        v
small set of exact unresolved contracts / relations / owners / hunks
        |
        v
high-resolution input for the coding LLM
```
