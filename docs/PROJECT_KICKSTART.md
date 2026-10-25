+++
schema = "kairos-context/v1"
id = "KAIROS_PROJECT_KICKSTART"
type = "documentation"
revision = 2
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_PROJECT_KICKSTART"
updated_at = "2026-09-09T08:45:00Z"
capsule = "Authoritative project-initiation procedure: framework search and Human intent first, universal detect/auto intake second, compiler-backed C-family authority where required, then exact Markdown/database baseline and Workshop handoff."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_PROJECT_KICKSTART"]
facets = ["kickstart", "intent", "universal-intake", "compiler", "ground-truth"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "project_kickoff"
question = "How do I initialize an existing C++ project with KAIROS?"
target = "s-1-project-intent-precedes-code-discovery"

[[answers]]
intent = "project_kickoff"
question = "What establishes project intent before KAIROS inspects code?"
target = "s-1-project-intent-precedes-code-discovery"

[[answers]]
intent = "compiler_authority"
question = "How does KAIROS determine which translation units belong to a project?"
target = "s-2-compiler-backed-implementation-binding"

[[answers]]
intent = "ground_truth"
question = "What is the ground-truth contract during KAIROS project intake?"
target = "s-ground-truth-contract"

[[answers]]
intent = "universal_intake"
question = "How do I initialize a Python Rust JavaScript TypeScript Go Java .NET Ruby PHP or mixed project with KAIROS?"
target = "s-universal-auto-intake"

[[search_contract]]
query = "How do I initialize an existing C++ project with KAIROS?"
expected = "KAIROS_PROJECT_KICKSTART#s-1-project-intent-precedes-code-discovery"
required_top_k = 1
+++
# Project kickstart and ground-truth binding

## CONTEXT INDEX

- [`s-overview`](#s-overview) — The canonical framework is intentionally unbound. A real project is created in two ordered
- [`s-1-project-intent-precedes-code-discovery`](#s-1-project-intent-precedes-code-discovery) — Generate the LLM prompt from the Human project outcome:
- [`s-2-compiler-backed-implementation-binding`](#s-2-compiler-backed-implementation-binding) — The command never edits the project source tree. It writes only the selected KAIROS
- [`s-ground-truth-contract`](#s-ground-truth-contract) — compile_commands.json is the primary translation-unit membership authority because it is
- [`s-materialized-evidence`](#s-materialized-evidence) — goals/<GOAL>.json and tasks/task_<TASK>.md: the reviewed Human project intent and active work scope, created before source discovery.
- [`s-handoff-to-synchronized-development`](#s-handoff-to-synchronized-development) — A verified intake may enter the separate Workshop lifecycle:
- [`s-continuing-after-the-first-baseline`](#s-continuing-after-the-first-baseline) — Ordinary code/header edits that keep compiler membership and the governed header set stable use

<a id="s-overview"></a>
## Overview

> Capsule: The canonical framework is intentionally unbound. A real project is created in two ordered

The canonical framework is intentionally unbound. A real project begins by retrieving the
framework onboarding contract, establishing Human intent, detecting the ecosystem(s), and binding
observed implementation reality under that exact goal/milestone/task scope. Static ecosystems use
bounded exact source membership; C-family translation units retain stronger compiler authority.

<a id="s-1-project-intent-precedes-code-discovery"></a>
## 1. Project intent precedes code discovery

> Capsule: Generate the LLM prompt from the Human project outcome:

Generate the LLM prompt from the Human project outcome:

```powershell
python -m kickstart prompt --idea "Describe the intended project outcome"
```

The LLM returns one reviewed `kairos-project-kickoff/v1` contract containing a unique
`GOAL_*`, one initial `MILESTONE_*`, one `TASK_*`, and measurable `CRIT_*` entries. The
contract describes desired outcomes. It must not infer project architecture from code that
has not yet been surveyed.

A fresh compiler intake refuses to run without this contract. An already initialized KAIROS
workspace may omit it only when its active goal/milestone/task scope already exists; if a
contract is supplied there, its identifiers must exactly match that active authority.

<a id="s-universal-auto-intake"></a>
## 2. Universal detect and auto intake

> Capsule: `detect` identifies supported ecosystems without mutating the project; `init --auto` creates the exact initial KAIROS/Workshop baseline while preserving compiler-backed C-family membership.

From the release root:

```text
python -m kickstart detect <project>
python -m kickstart init --auto --project-root <project> --workspace <fresh-kairos-workspace> --spec-file project-spec.json
```

Supported static ecosystems are Python, JavaScript/TypeScript, Rust, Go, Java/Kotlin/Groovy,
.NET, Ruby and PHP. Their initial authority is exact project-local source membership with byte
hashes and verbatim ledgers; dynamic imports, runtime reachability and build participation are not
invented. Generated/vendor trees are excluded.

If C/C++/CUDA translation units are detected, `--auto` additionally requires compiler-produced
membership:

```text
python -m kickstart init --auto --project-root <project> --workspace <fresh-kairos-workspace> --spec-file project-spec.json --auto-compile-commands <build>/compile_commands.json
```

Mixed projects combine static and compiler-backed authority without treating Python/Rust/etc. as
C-family translation units. Initial intake reads the governed project and writes the KAIROS workspace;
it does not modify the governed project. If an analysis/build tool could write project or build files,
it must run against an isolated clone/snapshot.

<a id="s-2-compiler-backed-implementation-binding"></a>
## 3. C-family compiler-backed implementation binding

> Capsule: The command never edits the project source tree. It writes only the selected KAIROS

```powershell
python -m kickstart init --project-root <project> --workspace <fresh-kairos-workspace> --spec-file project-spec.json --compile-commands <build>/compile_commands.json
python -m kickstart init --project-root <project> --workspace <fresh-kairos-workspace> --spec-file project-spec.json --cmake <project>/CMakeLists.txt
```

The command never edits the project source tree. It writes only the selected KAIROS
workspace, its derived Workshop configuration, retained compiler evidence and exact
Markdown mirrors.

<a id="s-ground-truth-contract"></a>
## Ground-truth contract

> Capsule: compile_commands.json is the primary translation-unit membership authority because it is

`compile_commands.json` is the primary translation-unit membership authority because it is
emitted by the compiler build graph. With `--cmake`, KAIROS executes an isolated CMake
configure with `CMAKE_EXPORT_COMPILE_COMMANDS=ON` and validates the emitted records. The
framework does not parse a binary, match object-file stems, or regex-search arbitrary CMake
prose as a source-set claim. Binary-only input is refused unless a future project-specific
debug/symbol adapter supplies an independently verifiable source mapping.

Each compiler-recorded translation unit must be an existing project-local C/C++/CUDA path
with UTF-8 bytes, supported suffix, normalized relative identity and no symlink/reparse
components. Distinct compile records for one translation unit are retained rather than
collapsed into an invented include order.

For the initial static include closure, KAIROS preserves include-root order per distinct
compiler command. Quoted includes check the including file's directory first; compiler
include roots are then examined in their recorded order. An existing external include root
acts as a stop point: KAIROS does not skip an external match and then falsely select a later
same-named project header. Transitive headers retain every translation-unit owner that
reaches them.

The static scanner intentionally does not guess preprocessor-computed includes such as
`#include SOME_MACRO`. Such a project fails intake with `DYNAMIC_INCLUDE_UNSUPPORTED`
unless a future compiler dependency adapter supplies the selected dependency. Unresolved
quoted project includes, path escapes, unsupported suffixes and ledger-unrepresentable
names also fail closed.

This is a conservative static local include authority, not a claim that KAIROS reimplements
the complete C/C++ preprocessor. The translation-unit set is compiler-produced; literal
header closure is compiler-context-guided and deliberately bounded.

<a id="s-materialized-evidence"></a>
## Materialized evidence

> Capsule: goals/<GOAL>.json and tasks/task_<TASK>.md: the reviewed Human project intent and active work scope, created before source discovery.

- `goals/<GOAL>.json` and `tasks/task_<TASK>.md`: the reviewed Human project intent and active work scope, created before source discovery.
- `code/PROJECT_CODE_<digest>.md`: one `kairos-context/v1` document per compiler-recorded translation unit, with exact mapping facts, raw/logical hashes and verbatim `C:` ledger.
- `code/PROJECT_HEADER_<digest>.md`: one header-only code document per project-local header in the admitted static include closure, with explicit empty source ledger and verbatim `H:` ledger.
- `docs/PROJECT_SOURCE_INDEX.md`: compiler membership plus current header-owner closure in Workshop authority format, promoted as a KAIROS document.
- `docs/PROJECT_KICKSTART.md`: project-scoped procedure and evidence boundary.
- `.kairos/project-intake/compile_commands.json`: retained compiler input; `.kairos/project-intake/PROJECT_BUILD_AUTHORITY.cmake` is the derived Workshop membership input.
- `.kairos/workshop.config.json`: project-bound Workshop configuration, including per-translation-unit include-root sequences.
- `.kairos/project-intake.json`: hashes, active project scope, optional project-intent hash, translation units, header closure and bounded intake result.

All generated Markdown is promoted through one KAIROS heartbeat. Promotion is the only
operation that writes the database projection. Intake then verifies build membership,
DATAFLOW membership, blueprint/managed byte parity, exact ledgers, header closure and owner
mapping, database projections and KAIROS health. A failure leaves evidence for diagnosis
but does not mark the project corpus verified.

<a id="s-handoff-to-synchronized-development"></a>
## Handoff to synchronized development

> Capsule: A verified intake may enter the separate Workshop lifecycle:

A verified universal intake returns `VERIFIED_PENDING_SEAL` and then enters Workshop:

```text
status -> seal
existing file: checkout -> edit transaction work tree -> metadata review -> prepare -> verify -> apply -> POSTCHECK_VERIFIED
source-set: source-set-checkout -> edit candidate tree -> source-set-prepare -> source-set-verify -> source-set-apply -> POSTCHECK_VERIFIED
```

After seal, Workshop is the only mutation path for governed source and governed Markdown. Direct
live edits followed by rediscovery or documentation repair are forbidden.

The first real Workshop patch is part of the portability contract: the native shadow carries
the authoritative KAIROS reference targets required by the changed code documents, so an
otherwise correct first patch does not fail merely because its task/method/source-index
references were omitted from the shadow.

Owner-only include-topology changes among an unchanged governed header set are updated in
`PROJECT_SOURCE_INDEX.md` inside the same Workshop transaction and promoted with the code
document. A change that adds or removes a governed local header is a stronger authority migration
and must use the explicit `authority-*` transaction described below; the normal transaction does
not silently grow or shrink the mapped corpus.

Initiation and Workshop verification establish source/document/database synchronization.
They do not by themselves prove a successful application build, runtime result, scientific
correctness, product release or customer acceptance unless a separately configured adapter
supplies that authority.

<a id="s-continuing-after-the-first-baseline"></a>
## Continuing after the first baseline

> Capsule: Ordinary code/header edits that keep compiler membership and the governed header set stable use

Ordinary code/header edits that keep compiler membership and the governed header set stable use
the normal Workshop transaction. Include-owner changes among already governed headers are derived
inside that same transaction and update both `PROJECT_SOURCE_INDEX.md` and affected header
`COMPILED OWNERS` sections before shadow verification.

A structural candidate that changes the governed local-header set while compiler translation-unit
membership and normalized compiler context remain unchanged uses an isolated candidate tree:

```powershell
python -m runtime_sync_workshop --config <workspace>/.kairos/workshop.config.json authority-checkout `
  --candidate-root <isolated-candidate> `
  --compile-commands <candidate-build>/compile_commands.json `
  --purpose "Describe the structural authority change"
python -m runtime_sync_workshop --config <workspace>/.kairos/workshop.config.json authority-prepare TXN_<id>
python -m runtime_sync_workshop --config <workspace>/.kairos/workshop.config.json authority-verify TXN_<id>
python -m runtime_sync_workshop --config <workspace>/.kairos/workshop.config.json authority-apply TXN_<id>
```

The checkout copies compiler-recorded translation units and the admitted local include closure into
transaction staging. It classifies header additions/removals and exact content changes. Review is
explicit: changed existing files use `updated`/`none`, new headers use `created`, and removed headers
use `removed`. Removed managed header documents become `state = "superseded"` historical evidence;
they are not silently erased from KAIROS knowledge.

`authority-verify` builds a candidate KAIROS database and Workshop corpus entirely from staged
code/documents/machine authority. Only a verified candidate may reach `authority-apply`, which
backs up affected live paths, applies the admitted topology, runs one governed heartbeat and full
health/corpus postcheck, then reseals. A pre-heartbeat failure rolls back; a failure after the
heartbeat boundary retains the lease as `RECOVERY_REQUIRED`.

### Build-system claim boundary

The generic authority migration deliberately refuses translation-unit membership or normalized
compiler-context changes with `BUILD_AUTHORITY_MIGRATION_REQUIRED`. A candidate compiler database
can prove what was presented to KAIROS, but it cannot make the live project's CMake/Meson/Bazel or
other build authority agree by itself. Such changes require a separately implemented, project-bound
build-authority migration that can update and reproduce the real build configuration. The generic
path therefore handles header-topology changes only while compiler membership/context remain stable.
