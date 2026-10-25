+++
schema = "kairos-context/v1"
id = "KAIROS_ARCHITECTURE"
type = "documentation"
revision = 1
state = "active"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_ARCHITECTURE"
updated_at = "2026-09-08T10:30:00Z"
capsule = "Stable architecture separating Human intent, compiler evidence, Markdown authority, derived retrieval, Workshop synchronization, and build-system boundaries."
claim_boundary = "This framework source owns the stable architecture it states; live project state, project-specific evidence, and execution results remain owned by their project authorities."
entities = ["KAIROS", "KAIROS_ARCHITECTURE"]
facets = ["architecture", "compiler-authority", "workshop", "build-system"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "architecture"
question = "How is KAIROS architecturally organized?"
target = "s-overview"

[[answers]]
intent = "compiler_authority"
question = "What owns translation-unit membership in KAIROS?"
target = "s-overview"

[[answers]]
intent = "build_authority"
question = "Why does KAIROS refuse to rewrite project build files by inference?"
target = "s-build-system-boundary"

[[answers]]
intent = "compiler_authority"
question = "What owns translation-unit membership?"
target = "s-overview"

[[search_contract]]
query = "How is KAIROS architecturally organized?"
expected = "KAIROS_ARCHITECTURE#s-overview"
required_top_k = 1
+++
# KAIROS architecture

## CONTEXT INDEX

- [`s-overview`](#s-overview) — KAIROS separates Human intent, implementation evidence and derived retrieval while keeping
- [`s-build-system-boundary`](#s-build-system-boundary) — Compiler databases are observed authority, not permission to rewrite a project's build language.

<a id="s-overview"></a>
## Overview

> Capsule: KAIROS separates Human intent, implementation evidence and derived retrieval while keeping

KAIROS separates Human intent, implementation evidence and derived retrieval while keeping
them in one governed control plane. The distribution also carries a separate immutable framework
knowledge projection so those governing rules are searchable before a project control plane exists.

1. A reviewed project goal/milestone/task contract owns intended outcomes before source discovery.
2. Compiler records own translation-unit membership; bounded include evidence owns the admitted local header closure.
3. Exact implementation Markdown carries mapping facts, hashes and source/header ledgers under that project scope.
4. The heartbeat validates source documents and promotes their section/graph projection into SQLite.
5. SQLite FTS and graph tables provide bounded retrieval and typed context chasing; they never replace source authority.
6. The Workshop binds live code, implementation documents, source-index topology and database projection into one sealed package and applies changes through isolated verification and postcheck.
7. Changes to the governed local-header set use a separate authority-migration transaction and are verified in an isolated KAIROS/Workshop shadow before live mutation. Translation-unit membership or compiler-context changes fail closed until a project-specific build-authority migration can reproduce the real build configuration. Removed header documents are retained as superseded historical evidence rather than silently deleted.
8. Dynamic routers expose the current frontier; receipts, archives and backups make transitions inspectable and recoverable.

The harness is intentionally dependency-light beyond Python and SQLite FTS5. A real external
project starts in a fresh workspace created from reviewed Human intent, not from the bundled
framework self-model. Compiler-backed intake then materializes the first synchronized code
and document baseline.

<a id="s-build-system-boundary"></a>
## Build-system boundary

> Capsule: Compiler databases are observed authority, not permission to rewrite a project's build language.

Compiler databases are observed authority, not permission to rewrite a project's build language.
Initial intake may ask CMake to emit `compile_commands.json`. The generic post-baseline authority
migration may change local-header topology only while the normalized compiler context and translation-
unit membership stay fixed. KAIROS does not infer edits to `CMakeLists.txt`, Meson, Bazel, build
generators or package manifests; changing those compiler authorities is a separate project-specific
build-authority migration and fails closed in the generic framework.
