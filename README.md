+++
schema = "kairos-context/v1"
id = "KAIROS_README"
type = "documentation"
revision = 2
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_README"
updated_at = "2026-10-25T18:32:39Z"
capsule = "Human entry surface for installing KAIROS, querying the immutable framework corpus, and starting a compiler-backed project workspace."
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

- [`s-overview`](#s-overview) — **Release: 0.1.0-alpha.2 — developer preview.** The cold distribution has been validated
- [`s-layout`](#s-layout) — | Path | Role |
- [`s-ask-kairos-how-kairos-works`](#s-ask-kairos-how-kairos-works) — The release ships a hash-verified read-only framework knowledge database. After installing the harness, the ordinary search command works before any project workspace exists:
- [`s-start-a-real-project`](#s-start-a-real-project) — Project intent is established before code discovery. First ask an LLM to formulate the

<a id="s-overview"></a>
## Overview

> Capsule: **Release: 0.1.0-alpha.2 — developer preview.** The cold distribution has been validated

**Release: 0.1.0-alpha.2 — developer preview.** The cold distribution has been validated
end-to-end on an external C++/CMake repository. This is not a stable/1.0 or production-support
claim. Read `docs/KNOWN_LIMITATIONS.md` before adopting it.


This directory is the project-agnostic KAIROS framework. It contains reusable context
governance, a deterministic harness, a derived SQLite projection, loop procedures, a
compiler-backed project intake edge, and a fail-closed code/document Workshop. It contains
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
| `kickstart/` | Human-intent + compiler-backed project initiation and exact initial source/header binding |
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

> Capsule: Project intent is established before code discovery. First ask an LLM to formulate the

Project intent is established before code discovery. First ask an LLM to formulate the
bounded KAIROS project contract from the Human goal:

```powershell
python -m kickstart prompt --idea "Describe the project outcome here"
```

Save the returned `kairos-project-kickoff/v1` JSON as `project-spec.json`, review it, then
bind the compiler-observed codebase under exactly that goal/milestone/task scope:

```powershell
python -m kickstart init `
  --project-root <project> `
  --workspace <fresh-kairos-workspace> `
  --spec-file project-spec.json `
  --compile-commands <build>/compile_commands.json
```

For CMake projects, replace `--compile-commands` with `--cmake <project>/CMakeLists.txt`.
The intake never edits the project source tree. It creates the project-scoped KAIROS
sources, exact code/header ledgers, the derived SQLite projection, Workshop configuration,
and a verified initial corpus. Only after that baseline passes should the Workshop be
sealed and used for synchronized changes.

Only general method sources under `workspace/docs/` belong to the public distribution.
Local goals, tasks, reports, databases, runtime state and development workspaces are excluded.
Create a fresh external project workspace; use the test commands in
`docs/EXTERNAL_AUDIT_SCOPE.md` to verify the framework itself.

See `docs/PROJECT_KICKSTART.md` for ground-truth and refusal boundaries,
`docs/WORKSHOP.md` for the synchronized change lifecycle, `docs/KNOWN_LIMITATIONS.md` for the
current support boundary, and `docs/RELEASE_NOTES_0.1.0_ALPHA2.md` for the framework-bootstrap release evidence. Changes to the governed local-header
set use the Workshop's explicit authority-migration commands against an isolated candidate tree.
Translation-unit membership or compiler-context changes fail closed until a project-specific
build-authority migration can reproduce the real build configuration; the framework never edits
a project's CMake/build files by inference.

Before any publication or remote write, freeze an exact file list and its exclusions and
obtain explicit approval. This package performs no Git or GitHub operation on its own.
