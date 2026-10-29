+++
schema = "kairos-context/v1"
id = "KAIROS_README"
type = "documentation"
revision = 3
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_README"
updated_at = "2026-10-29T23:00:00Z"
capsule = "Human entry surface for installing KAIROS, querying the immutable framework corpus, and starting a separately bound project workspace with exact source authority."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_README"]
facets = ["entry", "installation", "bootstrap", "kickstart"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "entry"
question = "How do I get started with KAIROS?"
target = "s-overview"

[[answers]]
intent = "framework_search"
question = "How can I ask KAIROS how KAIROS works before a project exists?"
target = "s-ask-kairos-how-kairos-works"

[[answers]]
intent = "project_kickoff"
question = "How do I start a real project with KAIROS?"
target = "s-start-a-real-project"

[[search_contract]]
query = "How do I get started with KAIROS?"
expected = "KAIROS_README#s-overview"
required_top_k = 1
+++
# KAIROS Framework

## CONTEXT INDEX

- [`s-overview`](#s-overview) — **Current technical snapshot: 2026-10-30 — developer preview.**
- [`s-layout`](#s-layout) — | Path | Role |
- [`s-ask-kairos-how-kairos-works`](#s-ask-kairos-how-kairos-works) — The release ships a hash-verified read-only framework knowledge database. After installing the harness, the ordinary search command works before any project workspace exists:
- [`s-start-a-real-project`](#s-start-a-real-project) — Review a bounded human contract before read-only detection and explicitly authorized project intake.

<a id="s-overview"></a>
## Overview

> Capsule: **Current technical snapshot: 2026-10-30 — developer preview.**

**Current technical snapshot: 2026-10-30 — developer preview.**
This snapshot includes bounded universal intake, framework tool routing, typed review,
transaction recycling and source-bound publication lineage checks. Full product-readiness
acceptance remains open; this is not a stable or production-support claim. Read `docs/KNOWN_LIMITATIONS.md` before adopting it.


This directory is the project-agnostic KAIROS framework. It contains reusable context
governance, a deterministic harness, a derived SQLite projection, loop procedures, a
bounded multi-language project intake edge, and a fail-closed code/document Workshop. It contains
no customer source tree, project-bound transaction history, seal, or pre-existing project
database.

<a id="s-layout"></a>
## Layout

> Capsule: | Path | Role |

| Path | Role |
|---|---|
| `kairos/kairos_harness/` | Python harness, schemas, retrieval, graph, governance, and document templates |
| `kairos/kairos_harness/kairos/framework.db` | Immutable derived projection of the headerized framework corpus for pre-project search |
| `kairos/kairos_harness/kairos/framework_manifest.json` | Hash binding between the framework projection and its admitted source documents |
| `workspace/` | General framework method documents only; no consumer workspace or development history |
| `workshop/` | Code/blueprint synchronization engine; unbound until project intake creates a config |
| `kickstart/` | Human-intent + exact-authority project initiation and exact initial source/header binding |
| `templates/` | Project-neutral configuration templates |
| `docs/` | Canonical architecture and operating procedures |

<a id="s-ask-kairos-how-kairos-works"></a>
## Ask KAIROS how KAIROS works

> Capsule: The release ships a hash-verified read-only framework knowledge database. After installing the harness, the ordinary search command works before any project workspace exists:

The release ships a hash-verified read-only framework knowledge database. No installation is
required for the bootstrap query surface: from the unpacked release root use the bundled wrapper:

```text
python kairos_cli.py search "How do I start a real project with KAIROS?"
python kairos_cli.py search "What owns translation-unit membership?"
python kairos_cli.py search "When is authority migration required?"
```

If the harness is installed (`python -m pip install -e ./kairos/kairos_harness`), the equivalent
console command is simply `kairos search ...`. Omitting `--workspace` searches the immutable
framework corpus and returns section-addressed authoritative Markdown. See
`docs/FRAMEWORK_BOOTSTRAP.md`.

<a id="s-start-a-real-project"></a>
## Start a real project

> Capsule: Review a bounded human contract before read-only detection and explicitly authorized project intake.

Render a prompt with the following command. It does not call an LLM:

```text
python -m kickstart prompt --idea "Describe the project outcome here"
```

Have the LLM produce a kairos-project-kickoff/v1 JSON contract, save it as project-spec.json,
and review its goal, milestone, task and acceptance criteria. Only then inspect the intended
project read-only and explicitly initialize its workspace:

```text
python -m kickstart detect <project>
python -m kickstart init --project-root <project> --workspace <fresh-kairos-workspace> --spec-file project-spec.json --auto
```

The framework checkout is the project environment, not the consumer project. The project
root and workspace may live outside it; their names are arbitrary. Always pass the explicit
project workspace when querying project knowledge. Keep the framework corpus separate.

Automatic intake supports bounded local Python, JavaScript/TypeScript, Rust, Go, JVM, .NET,
Ruby and PHP source membership. Exact files and hashes are authority; dynamic imports and
runtime reachability are not inferred. C/C++/CUDA require compiler-produced compile_commands.json:
use --compile-commands <build>/compile_commands.json, or --cmake <project>/CMakeLists.txt.
Mixed automatic intake uses --auto --auto-compile-commands <build>/compile_commands.json
for its C-family portion. See docs/PROJECT_KICKSTART.md for the support/refusal boundaries.

Intake observes project sources read-only and creates project-scoped Markdown, exact source
ledgers, the derived SQLite projection and Workshop configuration. After baseline validation,
seal and use the native Workshop lifecycle for synchronized changes:
status -> seal -> checkout -> edit code and metadata -> metadata-plan -> complete audit ->
prepare -> metadata-check -> verify -> apply. Document-only and source-topology changes use
their documented dedicated procedures. Project tests and domain validity require explicit
project-specific evidence.

Only general method sources under `workspace/docs/` belong to the public distribution.
Local goals, tasks, reports, databases, runtime state and development workspaces are excluded.
Create a fresh external project workspace; use the test commands in
`docs/EXTERNAL_AUDIT_SCOPE.md` to verify the framework itself.

See `docs/PROJECT_KICKSTART.md` for ground-truth and refusal boundaries,
`docs/WORKSHOP.md` for the synchronized change lifecycle, `docs/KNOWN_LIMITATIONS.md` for the
current support boundary, and `docs/RELEASE_NOTES_0.1.0_ALPHA2.md` for historical alpha.2 bootstrap evidence. Changes to the governed local-header
set use the Workshop's explicit authority-migration commands against an isolated candidate tree.
Translation-unit membership or compiler-context changes fail closed until a project-specific
build-authority migration can reproduce the real build configuration; the framework never edits
a project's CMake/build files by inference.

Before any publication or remote write, freeze an exact file list and its exclusions and
obtain explicit approval. This package performs no Git or GitHub operation on its own.
