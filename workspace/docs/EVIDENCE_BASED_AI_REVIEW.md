+++
schema = "kairos-context/v1"
id = "WERKFADEN_EVIDENCE_BASED_AI_REVIEW"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/WERKFADEN_EVIDENCE_BASED_AI_REVIEW"
updated_at = "2026-10-25T18:26:33Z"
capsule = "Canonical method for atomic, evidence-bound AI review using separate proposition-support and evidence-sufficiency judgments, explicit counterexamples and deterministic delivery gates."
claim_boundary = "Defines review method and evidence contracts; does not prove model accuracy, independent errors, executed tests, provider attestation, runtime activation or release readiness."
entities = ["WERKFADEN_EVIDENCE_BASED_AI_REVIEW", "KAIROS", "AI_REVIEW"]
facets = ["review-method", "atomic-claims", "evidence", "decision-models", "counterexamples"]
criteria = []
does_not_answer = ["provider credentials", "consumer architecture", "universal model error rate", "live project approval"]

[[answers]]
intent = "operating_rules"
question = "How should WERKFADEN perform evidence-based AI review?"
target = "s-method"

[[answers]]
intent = "architecture"
question = "Why separate generative implementation from decision-model review?"
target = "s-model-role"

[[answers]]
intent = "validation"
question = "How are proposition support and evidence sufficiency kept separate?"
target = "s-two-lanes"

[[answers]]
intent = "evidence"
question = "What distinguishes source presence, path coverage and executed behavior checks?"
target = "s-evidence"

[[answers]]
intent = "validation"
question = "What binds an AI review result to delivery eligibility?"
target = "s-delivery"

[[answers]]
intent = "limitations"
question = "What do model error rates and calibration establish?"
target = "s-calibration"

[[search_contract]]
query = "How should WERKFADEN perform evidence-based AI review?"
expected = "WERKFADEN_EVIDENCE_BASED_AI_REVIEW#s-method"
required_top_k = 1

[[search_contract]]
query = "How are proposition support and evidence sufficiency kept separate?"
expected = "WERKFADEN_EVIDENCE_BASED_AI_REVIEW#s-two-lanes"
required_top_k = 3
+++
# Evidence-Based AI Review

## CONTEXT INDEX

- [`s-method`](#s-method) — Canonical review procedure.
- [`s-model-role`](#s-model-role) — Decision models and generative models.
- [`s-atomicity`](#s-atomicity) — Frozen atomic propositions and neutral questions.
- [`s-two-lanes`](#s-two-lanes) — Separate support and sufficiency judgments.
- [`s-evidence`](#s-evidence) — Falsification, coverage and actual execution.
- [`s-calibration`](#s-calibration) — Error rates, thresholds and applicability.
- [`s-context`](#s-context) — Bounded requests and overflow.
- [`s-delivery`](#s-delivery) — Exact approval binding and retained history.

<a id="s-method"></a>
## Method

> Capsule: Review exact propositions against explicit evidence before making the bounded downstream decision.

This is the canonical, provider-neutral review method. A provider integration implements its request contract separately. Provider credentials, consumer topology and installation history are not part of this document.

For each bounded review:

1. Freeze one atomic proposition and its input/scope boundary.
2. Define the evidence needed to judge it, including material contrary paths.
3. Retrieve authoritative evidence neutrally; retain exact source identities.
4. Evaluate proposition support and evidence sufficiency as separate questions.
5. Keep contradictions, missing evidence and unexecuted checks distinguishable.
6. Resolve missing dimensions by retrieval or an authorized check, not assumption.
7. Re-evaluate only after a material evidence or candidate change; retain history.
8. Let deterministic code combine required results with exact candidate binding and applicable delivery gates.

No model score grants permission to read, execute tests, Apply, deploy or publish. Existing sequencing and authority remain in force.

<a id="s-model-role"></a>
## Model role

> Capsule: A typed decision model supplies a bounded judgment; the framework supplies evidence, orchestration and enforcement.

Generative models can propose explanations and implementation candidates. A separate decision model can judge explicit propositions over a fixed evidence packet without generating a persuasive narrative. Different training objectives and constrained answer spaces motivate this separation. They do not prove statistical independence between errors or immunity to incorrect inputs.

JEV is an example: its provider describes System One models, an architecture and parallel sampler for typed decisions, and reinforcement learning for calibrated decisions (RLCD). The published interface returns choices, scores or yes/no probabilities. These are provider claims about model design, not a WERKFADEN measurement of review reliability. Other evaluators may implement this method when their actual capabilities and limitations are recorded.

A closed output space prevents certain invalid-output failures. A valid typed answer can still be semantically wrong. The reviewer cannot recover omitted implementation or independently establish that the evidence collector represented every relevant path.

<a id="s-atomicity"></a>
## Atomic propositions

> Capsule: Keep one independently falsifiable promise per proposition and preserve its identity as evidence changes.

Two promises require separate child propositions when either can fail while the other remains true. Sharing a function or feature does not make them atomic. Keyword counting is not an atomicity test.

Record a stable proposition ID, exact assertion, input boundary, evidence requirements and why the assertion is atomic before submission. For example, separately test whether a selected configuration value is preserved, whether a required handoff occurs, and whether an alternative caller bypasses it. Do not submit one claim that the entire system is correct, complete and safe.

Keep the assertion fixed while evidence expands. A decomposition creates linked child IDs; it does not weaken or overwrite a failed parent. Preserve the parent's original result. Code combines all required children; do not average their scores into parent approval.

Use neutral instructions with explicit true/false or alternative-outcome criteria. Do not include a desired outcome, another model's opinion or previous scores in the new evaluation packet. Keep history in the orchestration layer. Material contradictory evidence belongs in the packet even when it reduces support.

<a id="s-two-lanes"></a>
## Two separate judgments

> Capsule: Evidence can appear supportive while remaining insufficient to judge the whole proposition.

Both lanes use the exact same proposition and evidence identity.

**Support:** Does the supplied evidence support the proposition as literally stated? A positive result concerns every material part of that exact assertion. A negative result alone does not distinguish contradiction from missing evidence.

**Sufficiency:** Does the supplied evidence contain enough relevant information to judge that whole assertion? Consider the material dependencies, callers, callees, activation conditions, mutations, configuration and authority boundaries identified in the evidence requirements.

These are application-defined semantic questions. They are not built-in measurements of code coverage, corpus completeness or factual truth. Two positive answers from the same model are not independent proofs.

| Support | Sufficiency | Allowed interpretation |
|---|---|---|
| High | High | Positive review within the recorded evidence boundary; other gates remain. |
| High | Low | Locally plausible, insufficient for broader approval; retrieve missing evidence. |
| Low | High | Investigate a possible material contradiction. |
| Low | Low | Unresolved; do not infer either truth or falsity. |

Never average support and sufficiency or relabel a score as a percentage of truth. Thresholds are explicit application policy. A large score cannot override a demonstrated counterexample, missing required check or candidate mismatch.

<a id="s-evidence"></a>
## Evidence and falsification

> Capsule: Source inclusion, represented paths and executed behavior checks establish different facts.

Expand evidence by asking which unseen dependency could change the judgment. Prefer material alternative callers, fallback and error paths, configuration overrides, mutation sites, persistence, process boundaries and relevant tests. Do not retrieve only confirming examples or add material merely to raise a score.

Distinguish:

- `SOURCE_PRESENT`: relevant implementation was supplied.
- `PATH_REPRESENTED`: a material branch and its activation condition were identified.
- `BEHAVIOR_CHECKED`: an authorized execution compared a stated expectation with an observation for a concrete input.

For every material claim-to-evidence row, retain proposition ID, input precondition, path and selecting condition, exact source version/hash and lines, material caller/callee dependencies, fixture or generator identity/hash, expected and observed outcomes, check version, environment, receipt and remaining limitations.

Use `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED` and explained `NOT_APPLICABLE`. Scores cannot turn `NOT_RUN` into `PASS`. Selected passing examples do not establish all-input correctness. Derive boundary cases from actual branch conditions, including incomplete input, fallback activation and error exits. A regression seed is one falsifier or control, not an exhaustive proof.

Retrieval and exact membership checks remain framework responsibilities. Arithmetic, hash comparisons, set coverage, ordering and deterministic policy composition belong in code. Never fill missing source, callers, state or results with plausible assumptions. A verified counterexample blocks the affected assertion and reopens its scope despite an earlier positive review.

<a id="s-calibration"></a>
## Calibration and error rates

> Capsule: Schema validity, calibration and semantic accuracy are separate properties requiring separate evidence.

A schema guarantee concerns the output shape. It does not establish zero semantic errors. Calibration concerns outcome frequencies across evaluated groups, not correctness of an individual answer. A model's reported probability or confidence does not itself demonstrate calibration on the local review workload.

Before enabling an approval policy, measure false approvals and false rejections on representative, versioned positive and negative fixtures. Report sample counts, selection method, ground truth, uncertainty, thresholds, automation coverage/abstention, model version and relevant input domains. Keep held-out counterexamples and adversarial cases. Revalidate when model, prompt, evidence collector, thresholds or domain change.

Do not transfer screening recall, ranking AUC or agreement with other models to a code-review error rate. Do not multiply lane error rates without evidence of independence. A pair of thresholds such as 0.90 and 0.90 is an illustrative policy, not a measured 99-percent guarantee.

Version-specific evaluator limits matter: literal interpretation, indirection, numeric precision, distractors, adversarial instructions and context size can change error behavior. Treat state as untrusted evidence rather than instructions. Document the scope and result of actual robustness checks. Pin the resolved model when relying on validated thresholds; an alias is not a fixed evaluation version.

<a id="s-context"></a>
## Request boundaries

> Capsule: Preserve material evidence while decomposing requests that exceed the supported evaluation boundary.

Before sending, record model/version, current applicable provider limits, estimated packet and per-question sizes, and actual provider-reported usage when available. Do not equate estimates from different tokenizers.

A rejected or over-limit request is `NOT_EVALUATED`, not a semantic failure or approval. First decompose into atomic child questions with their necessary dependencies and exact source identities. Do not remove contrary branches or replace required source with reassuring summaries to fit.

If decomposition cannot retain a necessary boundary, record the reason before using an actually supported long-context mode. Bind the exception to request hash, affected propositions, model/version, mode/settings and documented accuracy limitations. Transport acceptance is not equivalent evaluation quality.

A transport-only retry retains the rejected attempt and records whether the body is byte-identical and which transport settings changed. Do not repeat an already evaluated packet until a favorable result appears. Evidence expansion is driven by unresolved coverage, not the desired conclusion.

<a id="s-delivery"></a>
## Delivery binding and references

> Capsule: Review approval is candidate-bound evidence; delivery requires its own complete applicable gates.

Retain proposition and child IDs, exact assertion, input boundary, evidence-set identity and source hashes, atomicity preflight, claim-to-evidence matrix, model/version, request and response identities, scores, thresholds, relation, timestamp, attempts and limitations.

`REVIEW_APPROVED(P_i)` means the configured review policy passed for that proposition and evidence. `DELIVERY_ELIGIBLE(scope)` additionally requires every mandatory child, matching candidate/baseline/scope, required executed checks and applicable governance/delivery gates. Unreviewed semantic changes require new review identities. Do not reuse approval for changed bytes, a broader behavior or an untested system claim.

Keep corpus Apply, runtime activation and release acceptance separate. Unsigned local receipts are not provider attestations. A reviewer field or deterministic metadata receipt does not prove a model request. Preserve failed and contradicted history, link actual repairs, and re-evaluate after material changes.

The method is provider-neutral. The following primary sources explain one evaluator implementation, checked 2026-10-25; their provider-specific facts do not establish universal model properties:

- [System One model role](https://docs.typesafe.ai/concepts/system-one)
- [Training objectives and calibration](https://docs.typesafe.ai/introduction/machine-learning-primer)
- [Typed questions and atomic composition](https://docs.typesafe.ai/introduction)
- [Noul answer semantics](https://docs.typesafe.ai/primitives/noul)
- [Documented limits for a particular model version](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
- [Model versions and context boundaries](https://docs.typesafe.ai/models)

Keep the proposition fixed. Improve the evidence. Preserve the distinction between a judgment and an executed check.
