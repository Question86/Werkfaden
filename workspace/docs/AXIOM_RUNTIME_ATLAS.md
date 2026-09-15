+++
schema = "kairos-context/v1"
id = "AXIOM_RUNTIME_ATLAS"
type = "documentation"
revision = 1
state = "active"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK_CANONICAL_20260907"
route = "KAIROS_FRAMEWORK_CANONICAL_20260907/AXIOM_RUNTIME_ATLAS"
updated_at = "2026-09-15T22:38:00Z"
capsule = "Stable semantic navigation atlas for AXIOM Runtime responsibility codes A through L, including D2/D3/D4 and F2, without claiming current execution order."
claim_boundary = "This atlas owns semantic Runtime responsibility identities and navigation only. It does not prove current source bytes, current run participation, current execution order, scientific validity, test status, or release state."
entities = ["AX.RT", "AX.RT.A", "AX.RT.B", "AX.RT.C", "AX.RT.D", "AX.RT.D2", "AX.RT.D3", "AX.RT.D4", "AX.RT.E", "AX.RT.F", "AX.RT.F2", "AX.RT.G", "AX.RT.H", "AX.RT.I", "AX.RT.J", "AX.RT.K", "AX.RT.L"]
facets = ["axiom-runtime", "semantic-atlas", "architecture", "navigation"]
criteria = []
does_not_answer = ["current execution order", "current source bytes", "current Runtime state", "current test result", "release acceptance"]

[[answers]]
intent = "architecture"
question = "What is the top-level semantic architecture of the AXIOM Runtime?"
target = "s-overview"

[[answers]]
intent = "architecture"
question = "Which AXIOM Runtime responsibility code covers detector identity and geometry?"
target = "s-e"

[[answers]]
intent = "architecture"
question = "Which AXIOM Runtime responsibility codes cover content evidence, decomposition, CFAR, regionization, joint null, and verdict?"
target = "s-f-to-i"

[[answers]]
intent = "authority"
question = "Does the AXIOM Runtime atlas prove current execution order?"
target = "s-authority-boundary"

[refs]
method = "[ref:docs/HYPOTHESIS_TO_PATCH_LOOP.md#s-map|id:KAIROS_HYPOTHESIS_TO_PATCH_LOOP|v:1|rel:requires|tags:investigation,architecture|src:declared]"

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.A"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-a-to-c"
evidence = "A is the intake boundary skeleton."

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.B"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-a-to-c"
evidence = "B is canonical and normalization gates."

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.C"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-a-to-c"
evidence = "C is the worker boundary / DetectorInputV1."

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.D"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-d-family"
evidence = "D is pipeline hash chain and state writer."

[[relations]]
subject = "AX.RT.D"
predicate = "defines"
object = "AX.RT.D2"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-d-family"
evidence = "D2 is hard-gate support interfaces."

[[relations]]
subject = "AX.RT.D"
predicate = "defines"
object = "AX.RT.D3"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-d-family"
evidence = "D3 is live state, proof, reliability."

[[relations]]
subject = "AX.RT.D"
predicate = "defines"
object = "AX.RT.D4"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-d-family"
evidence = "D4 is v2 API and Docker surface."

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.E"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-e"
evidence = "E is detector identity and geometry."

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.F"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-f-to-i"
evidence = "F is the content evidence cube."

[[relations]]
subject = "AX.RT.F"
predicate = "defines"
object = "AX.RT.F2"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-f-to-i"
evidence = "F2 is native/CUDA execution carrier."

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.G"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-f-to-i"
evidence = "G is decomposition and H0 null."

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.H"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-f-to-i"
evidence = "H is local CFAR and TBD regionizer."

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.I"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-f-to-i"
evidence = "I is joint region null and verdict."

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.J"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-j-to-l"
evidence = "J is legacy reader and contamination guard."

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.K"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-j-to-l"
evidence = "K is contract hash testsuite."

[[relations]]
subject = "AX.RT"
predicate = "defines"
object = "AX.RT.L"
object_kind = "concept"
scope = "semantic-runtime-responsibility"
evidence_target = "s-j-to-l"
evidence = "L is end-to-end acceptance gate."

[[search_contract]]
query = "What is the top-level semantic architecture of the AXIOM Runtime?"
expected = "AXIOM_RUNTIME_ATLAS#s-overview"
required_top_k = 3
+++
# AXIOM Runtime Semantic Atlas

## CONTEXT INDEX

- [`s-overview`](#s-overview) — Stable A-L semantic responsibility map used to orient investigation before run evidence and code detail.
- [`s-a-to-c`](#s-a-to-c) — Intake, canonicalization/normalization, and worker handoff responsibilities.
- [`s-d-family`](#s-d-family) — Proof/state and hard-gate/live/API support responsibilities D, D2, D3, D4.
- [`s-e`](#s-e) — Detector identity and geometry responsibility.
- [`s-f-to-i`](#s-f-to-i) — Content evidence, native/CUDA carrier, decomposition/H0, CFAR/TBD, joint null, and verdict responsibilities.
- [`s-j-to-l`](#s-j-to-l) — Contamination guard, contract-hash testsuite, and end-to-end acceptance responsibilities.
- [`s-authority-boundary`](#s-authority-boundary) — Semantic responsibility codes are not current execution order or live implementation proof.
- [`s-drilldown`](#s-drilldown) — Move from semantic code to current run evidence, graph, implementation sections, and exact source lines.

<a id="s-overview"></a>
## TOP-LEVEL SEMANTIC MAP

> Capsule: Stable A-L semantic responsibility map used to orient investigation before run evidence and code detail.

```text
AX.RT
├─ A   Intake boundary skeleton
├─ B   Canonical and normalization gates
├─ C   Worker boundary / DetectorInputV1
├─ D   Pipeline hash chain and state writer
│  ├─ D2  Hard-gate support interfaces
│  ├─ D3  Live state, proof, reliability
│  └─ D4  v2 API and Docker surface
├─ E   Detector identity and geometry
├─ F   Content evidence cube
│  └─ F2  Native/CUDA execution carrier
├─ G   Decomposition and H0 null
├─ H   Local CFAR and TBD regionizer
├─ I   Joint region null and verdict
├─ J   Legacy reader and contamination guard
├─ K   Contract hash testsuite
└─ L   End-to-end acceptance gate
```

Use these codes to name responsibility, not to manufacture current execution order.

<a id="s-a-to-c"></a>
## A-C — INTAKE TO WORKER BOUNDARY

> Capsule: A-C cover bounded intake, canonical/normalization gates, and the worker / DetectorInputV1 handoff.

- `AX.RT.A` — **Intake boundary skeleton.** Raw source enters through a bounded intake responsibility.
- `AX.RT.B` — **Canonical and normalization gates.** The Runtime validates and normalizes what the admitted payload is allowed to mean.
- `AX.RT.C` — **Worker boundary / DetectorInputV1.** Canonical input becomes the governed machine-usable worker/detector handoff surface.

<a id="s-d-family"></a>
## D FAMILY — PROOF, STATE, HARD GATES, LIVE/API SUPPORT

> Capsule: D, D2, D3, and D4 name proof/state and support responsibilities; their shared letter is taxonomy, not proof of adjacency in current execution.

- `AX.RT.D` — **Pipeline hash chain and state writer.** Formal proof/state anchoring responsibility.
- `AX.RT.D2` — **Hard-gate support interfaces.** Admission and hard-gate support responsibility.
- `AX.RT.D3` — **Live state, proof, reliability.** Downstream live/proof/reliability projection responsibility.
- `AX.RT.D4` — **v2 API and Docker surface.** Delivery/API/container projection responsibility.

Do not sort the current Runtime by these names. Current execution order belongs to current run/runtime evidence.

<a id="s-e"></a>
## E — DETECTOR IDENTITY AND GEOMETRY

> Capsule: E names the detector identity, tensor geometry, and routing responsibility boundary.

`AX.RT.E` is the semantic entry point for questions about detector identity, detector input geometry, and the point where detector routing becomes fixed. Use it to orient a detector investigation, then descend into current run evidence and implementation documents before reading source.

<a id="s-f-to-i"></a>
## F-I — PRODUCTIVE DETECTOR / NULL / REGION RESPONSIBILITIES

> Capsule: F through I cover content evidence, native/CUDA execution carrier, decomposition/H0, local CFAR/TBD, and joint-null/verdict responsibilities.

- `AX.RT.F` — **Content evidence cube.** Controlled materialization of observed content evidence.
- `AX.RT.F2` — **Native/CUDA execution carrier.** Productive backend execution-carrier responsibility.
- `AX.RT.G` — **Decomposition and H0 null.** Decomposition against explicit H0/null responsibility.
- `AX.RT.H` — **Local CFAR and TBD regionizer.** Local calibration/thresholding and bounded candidate-region responsibility.
- `AX.RT.I` — **Joint region null and verdict.** Joint-null and verdict-formation responsibility.

These labels are semantic waypoints. Which exact current modules/stages implement them must be obtained from current run evidence and promoted implementation documentation.

<a id="s-j-to-l"></a>
## J-L — CONTAMINATION, CONTRACT TESTS, ACCEPTANCE

> Capsule: J-L name contamination control, contract-hash testing, and final bounded acceptance responsibilities.

- `AX.RT.J` — **Legacy reader and contamination guard.** Legacy assumptions, negative controls, and contamination risk cross an explicit guard responsibility.
- `AX.RT.K` — **Contract hash testsuite.** Contract/fixture hash-governed test responsibility.
- `AX.RT.L` — **End-to-end acceptance gate.** Final bounded acceptance responsibility.

<a id="s-authority-boundary"></a>
## AUTHORITY BOUNDARY

> Capsule: Semantic responsibility codes are not current execution order or live implementation proof.

This atlas answers **what responsibility a code names**. It does not answer **what executed in the latest run**, **which current source file implements it**, or **whether that implementation is valid**.

Use separate authorities:

```text
semantic responsibility        -> this atlas
observed current execution      -> current run/runtime evidence
implementation ownership/detail -> promoted code/architecture documents
exact current bytes             -> governed source inspection
validation / release state      -> owning test/report/goal/gate authority
```

Never infer stage adjacency, caller/callee relations, producer/consumer edges, or current source ownership from the A-L labels alone.

<a id="s-drilldown"></a>
## DRILL-DOWN CONTRACT

> Capsule: Move from semantic code to current run evidence, graph, implementation sections, and exact source lines.

For an implementation problem:

1. choose the smallest relevant `AX.RT.*` semantic responsibility code;
2. verify the current run/runtime evidence that places concrete execution stages or artifacts under that responsibility;
3. use graph relations to identify ownership, producers/consumers, dependencies, calls, gates, and evidence anchors;
4. open only the exact implementation-document sections attached to the bounded causal surface;
5. counterprobe the context for a missing owner/bypass;
6. predict the exact source evidence expected;
7. inspect only the permitted source lines needed to test that prediction.

This drill-down is subordinate to `KAIROS_HYPOTHESIS_TO_PATCH_LOOP`: two independent falsifiers must support the problem frame before implementation mapping begins.
