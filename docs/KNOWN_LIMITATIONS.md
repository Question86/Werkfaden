+++
schema = "kairos-context/v1"
id = "KAIROS_KNOWN_LIMITATIONS"
type = "documentation"
revision = 2
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_KNOWN_LIMITATIONS"
updated_at = "2026-10-25T18:32:39Z"
capsule = "Current developer-preview support and refusal boundaries for project intake, structural migration, verification, model integration and licensing."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_KNOWN_LIMITATIONS"]
facets = ["limitations", "support-boundary", "fail-closed"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "limitations"
question = "What are the current limitations of KAIROS?"
target = "s-overview"

[[answers]]
intent = "project_scope"
question = "Which project languages and compiler evidence are supported by KAIROS intake?"
target = "s-project-intake-scope"

[[answers]]
intent = "migration"
question = "Which structural migrations does KAIROS refuse without refreshed build authority?"
target = "s-structural-migration-scope"

[[answers]]
intent = "verification"
question = "What does a successful KAIROS Workshop postcheck not prove?"
target = "s-verification-boundary"

[[search_contract]]
query = "What are the current limitations of KAIROS?"
expected = "KAIROS_KNOWN_LIMITATIONS#s-overview"
required_top_k = 1
+++
# Known limitations — 0.1.0-alpha.2

## CONTEXT INDEX

- [`s-overview`](#s-overview) — KAIROS 0.1.0-alpha.2 is a developer preview. The verified core is intended for technically
- [`s-project-intake-scope`](#s-project-intake-scope) — The project-binding edge currently targets C, C++ and CUDA translation units represented
- [`s-structural-migration-scope`](#s-structural-migration-scope) — Ordinary source/header edits and include-owner changes use the normal Workshop transaction.
- [`s-verification-boundary`](#s-verification-boundary) — KAIROS verifies synchronization among admitted source/header bytes, Markdown authority,
- [`s-product-and-model-integration-scope`](#s-product-and-model-integration-scope) — kickstart prompt renders the bounded prompt for an LLM to create the initial
- [`s-release-and-license-scope`](#s-release-and-license-scope) — This is an alpha/developer-preview release, not 1.0, stable, enterprise-ready, or a safety-

<a id="s-overview"></a>
## Overview

> Capsule: KAIROS 0.1.0-alpha.2 is a developer preview. The verified core is intended for technically

KAIROS 0.1.0-alpha.2 is a developer preview. The verified core is intended for technically
experienced users who are comfortable with compiler databases, command-line tooling, and
fail-closed workflows. This release is not a stable compatibility or production-support
promise.

<a id="s-project-intake-scope"></a>
## Project intake scope

> Capsule: The project-binding edge currently targets C, C++ and CUDA translation units represented

- The project-binding edge currently targets C, C++ and CUDA translation units represented
  by compiler-produced `compile_commands.json`, or CMake projects that can emit that database.
- Source/header intake requires UTF-8 regular files with representable project-relative paths.
  Symlink/reparse components are refused where an exact byte identity cannot be guaranteed.
- Translation-unit membership comes from compiler evidence. KAIROS does not infer membership
  from binary names, object-file stems, or arbitrary build-file text.
- The local header closure is deliberately conservative. Literal quoted/angle includes are
  resolved with retained compiler context; preprocessor-computed includes such as
  `#include SOME_MACRO` are not guessed and may fail closed.
- This release does not claim to reimplement the complete C/C++ preprocessor. When retained
  compiler evidence cannot establish a safe include decision, refusal is preferred over a
  plausible but unverified project fact.

<a id="s-structural-migration-scope"></a>
## Structural migration scope

> Capsule: Ordinary source/header edits and include-owner changes use the normal Workshop transaction.

- Ordinary source/header edits and include-owner changes use the normal Workshop transaction.
- Adding/removing governed local headers can use the explicit authority-migration transaction
  while translation-unit membership and normalized compiler context stay unchanged.
- Translation-unit additions/removals/renames or compiler-context changes require refreshed
  real build authority. The generic migration path intentionally refuses to edit CMake, Meson,
  Bazel, Make, IDE project files, or equivalent build sources by inference.
- Removed header documents are retained as superseded KAIROS history rather than silently
  deleted from the knowledge layer.

<a id="s-verification-boundary"></a>
## Verification boundary

> Capsule: KAIROS verifies synchronization among admitted source/header bytes, Markdown authority,

- KAIROS verifies synchronization among admitted source/header bytes, Markdown authority,
  derived SQLite projections, Workshop transaction state and content hashes.
- A successful Workshop postcheck does not by itself prove that the application builds, runs,
  passes domain tests, or is scientifically correct. Those claims require project-specific
  build/test/parity adapters or separately retained external evidence.
- The Workshop uses a global exclusive lease for governed mutations. This alpha is deliberately
  serialized rather than a concurrent multi-writer system.
- Logical Workshop guards do not automatically install operating-system ACLs. External editors
  can still bypass application-level rules unless the operator applies an appropriate filesystem
  boundary. See `workshop/SECURITY_BOUNDARY.md` and `acl-plan`.

<a id="s-product-and-model-integration-scope"></a>
## Product and model integration scope

> Capsule: kickstart prompt renders the bounded prompt for an LLM to create the initial

- The bundled framework database is a read-only search projection of the admitted core Markdown,
  not a project database and not an independent source of truth. Workspace-free bootstrap search
  verifies its packaged hash and, in the source distribution, the admitted Markdown hashes before
  answering.
- `kickstart prompt` renders the bounded prompt for an LLM to create the initial
  `kairos-project-kickoff/v1` contract. This release does not call or bundle an LLM provider.
  The returned contract must be reviewed before compiler discovery.
- Public `workspace/docs/` contains general framework methods only. Local project goals,
  tasks, reports and state are excluded; real project work uses a fresh external workspace.
- There is no GUI or one-click installer in this alpha. The supported interface is the Python
  command line plus the project compiler/CMake toolchain.
- The reusable KAIROS harness is broader than the current project-intake adapter, but this
  release should not be advertised as language-agnostic onboarding for arbitrary repositories.

<a id="s-release-and-license-scope"></a>
## Release and license scope

> Capsule: This is an alpha/developer-preview release, not 1.0, stable, enterprise-ready, or a safety-

- This is an alpha/developer-preview release, not `1.0`, stable, enterprise-ready, or a safety-
  critical compliance claim.
- The current `LICENSE` grants personal, private, non-commercial use only. The repository is
  source-available under that license; it is not an OSI open-source license and does not grant
  commercial, professional, organizational, redistribution, or hosting rights without written
  permission from the copyright holder.
- The cold release validation for this version was executed in an isolated Linux environment.
  Project-specific compilers, CMake generators, filesystem semantics and operating-system
  permissions can expose additional portability issues and should be validated before relying on
  KAIROS for a new environment.
