+++
schema = "kairos-context/v1"
id = "KAIROS_FRAMEWORK_BOOTSTRAP"
type = "documentation"
revision = 2
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_FRAMEWORK_BOOTSTRAP"
updated_at = "2026-09-09T08:45:00Z"
capsule = "Defines the pre-project bootstrap search: an agent can query hash-verified KAIROS rules before any project workspace or project database exists."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_FRAMEWORK_BOOTSTRAP"]
facets = ["bootstrap", "framework-search", "retrieval", "onboarding"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "framework_search"
question = "How do I query KAIROS rules before a project workspace exists?"
target = "s-before-project-intake"

[[answers]]
intent = "framework_search"
question = "How does framework bootstrap hand off into a project?"
target = "s-handoff-into-a-project"

[[answers]]
intent = "framework_search"
question = "What is the difference between framework search and project search?"
target = "s-framework-search-versus-project-search"

[[answers]]
intent = "onboarding"
question = "Where does framework bootstrap hand off to universal project onboarding?"
target = "s-handoff-into-a-project"

[refs]
rules = "[ref:AGENTS.md#s-mandatory-context-loop|id:KAIROS_FRAMEWORK_RULES|v:1|rel:requires|tags:rules,bootstrap|src:framework]"
search = "[ref:kairos/kairos_harness/docs/SEARCH_INDEX.md#s-kairos-search-query|id:KAIROS_SEARCH_INDEX|v:1|rel:requires|tags:search,retrieval|src:framework]"
kickstart = "[ref:docs/PROJECT_KICKSTART.md#s-universal-auto-intake|id:KAIROS_PROJECT_KICKSTART|v:2|rel:next|tags:kickstart,auto|src:framework]"
onboarding = "[ref:docs/UNIVERSAL_ONBOARDING.md#s-cold-onboarding|id:KAIROS_UNIVERSAL_ONBOARDING|v:1|rel:next|tags:onboarding,universal|src:framework]"

[[search_contract]]
query = "How do I query KAIROS rules before a project workspace exists?"
expected = "KAIROS_FRAMEWORK_BOOTSTRAP#s-before-project-intake"
required_top_k = 1
+++
# Framework bootstrap retrieval

## CONTEXT INDEX

- [`s-overview`](#s-overview) — KAIROS carries an immutable, hash-verified framework knowledge projection so an agent can ask how the framework must be used before any project workspace, goal, task, runtime state, or project database exists. The framework Markdown remains
- [`s-before-project-intake`](#s-before-project-intake) — Install the harness from kairos/kairos_harness, then use the ordinary kairos search command without --workspace to query framework rules. This path creates no project state and writes no retrieval trace.
- [`s-framework-search-versus-project-search`](#s-framework-search-versus-project-search) — A search without --workspace uses only the immutable framework corpus. A search with --workspace <path> uses the mutable project corpus and its normal freshness/governance boundary. Keeping the two projections separate prevents framework ru
- [`s-bootstrap-query-set`](#s-bootstrap-query-set) — Use question-shaped queries rather than asking the model to reconstruct rules from directory layout. Useful first questions include how project intent is established, what the compiler owns, how source inspection is permitted, how the Works
- [`s-authority-boundary`](#s-authority-boundary) — Framework search routes inspection; it does not create truth. Each result retains the owning Markdown document, section, authority, claim boundary, and content hash. The framework database is verified against a manifest of admitted source d
- [`s-handoff-into-a-project`](#s-handoff-into-a-project) — After the Human goal is converted into a reviewed kairos-project-kickoff/v1 contract, project intake may create a fresh project workspace and project database from compiler evidence. From that point, project questions use kairos search --wo

<a id="s-overview"></a>
## Overview

> Capsule: KAIROS carries an immutable, hash-verified framework knowledge projection so an agent can ask how the framework must be used before any project workspace, goal, task, runtime state, or project database exists. The framework Markdown remains

KAIROS carries an immutable, hash-verified framework knowledge projection so an agent can ask how the framework must be used before any project workspace, goal, task, runtime state, or project database exists. The framework Markdown remains authoritative; the bundled SQLite file is only a derived search projection.

<a id="s-before-project-intake"></a>
## Before project intake

> Capsule: Install the harness from kairos/kairos_harness, then use the ordinary kairos search command without --workspace to query framework rules. This path creates no project state and writes no retrieval trace.

From an unpacked source release, no installation is required: run `python kairos_cli.py search "<question>"` from the release root. The wrapper delegates to the bundled harness and queries framework rules without `--workspace`. If the harness is installed from `kairos/kairos_harness`, the equivalent console command is `kairos search "<question>"`. Both paths create no project state and write no retrieval trace.

Example:

```text
python kairos_cli.py search "How do I start a real project with KAIROS?"
python kairos_cli.py search "What owns translation-unit membership?"
python kairos_cli.py search "When is authority migration required?"
```

The returned paths point back to the authoritative framework Markdown sections.

<a id="s-framework-search-versus-project-search"></a>
## Framework search versus project search

> Capsule: A search without --workspace uses only the immutable framework corpus. A search with --workspace <path> uses the mutable project corpus and its normal freshness/governance boundary. Keeping the two projections separate prevents framework ru

A search without `--workspace` uses only the immutable framework corpus. A search with `--workspace <path>` uses the mutable project corpus and its normal freshness/governance boundary. Keeping the two projections separate prevents framework rules from being copied into every project database or being mistaken for project evidence.

<a id="s-bootstrap-query-set"></a>
## Bootstrap query set

> Capsule: Use question-shaped queries rather than asking the model to reconstruct rules from directory layout. Useful first questions include how project intent is established, what the compiler owns, how source inspection is permitted, how the Works

Use question-shaped queries rather than asking the model to reconstruct rules from directory layout. For a new codebase, ask `How do I onboard an arbitrary existing codebase into KAIROS?`, then ask `After KAIROS seals a project, what is allowed to modify source code or Markdown?`. The returned universal onboarding contract supplies `detect`, `--auto`, the C-family compiler boundary, clone-before-risk, ordinary Workshop mutation and source-set transactions.

<a id="s-authority-boundary"></a>
## Authority boundary

> Capsule: Framework search routes inspection; it does not create truth. Each result retains the owning Markdown document, section, authority, claim boundary, and content hash. The framework database is verified against a manifest of admitted source d

Framework search routes inspection; it does not create truth. Each result retains the owning Markdown document, section, authority, claim boundary, and content hash. The framework database is verified against a manifest of admitted source documents before a bootstrap query is served.

<a id="s-handoff-into-a-project"></a>
## Handoff into a project

> Capsule: After the Human goal is converted into a reviewed kairos-project-kickoff/v1 contract, project intake may create a fresh project workspace and project database from compiler evidence. From that point, project questions use kairos search --wo

After framework search retrieves the universal onboarding contract, the agent runs `kickstart detect <project>`, establishes the reviewed Human `kairos-project-kickoff/v1` contract, and runs `kickstart init --auto`. Static ecosystems use bounded exact source membership; C/C++/CUDA remains compiler-backed. Intake observes the original project read-only, creates the initial KAIROS/Workshop baseline, and returns `VERIFIED_PENDING_SEAL`. The agent then seals through Workshop. From that point, project questions use `kairos search --workspace <path> ...`; framework-rule questions remain available through the workspace-free search path, and all governed mutations go through Workshop transactions.
