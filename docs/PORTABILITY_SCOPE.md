+++
schema = "kairos-context/v1"
id = "KAIROS_PORTABILITY_SCOPE"
type = "documentation"
revision = 2
state = "active"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_PORTABILITY_SCOPE"
updated_at = "2026-10-25T18:32:39Z"
capsule = "Defines what the reusable framework package includes, excludes as authority, and requires for portability acceptance."
claim_boundary = "This framework source owns the stable architecture it states; live project state, project-specific evidence, and execution results remain owned by their project authorities."
entities = ["KAIROS", "KAIROS_PORTABILITY_SCOPE"]
facets = ["portability", "distribution", "acceptance"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "portability"
question = "What is included in the portable KAIROS framework?"
target = "s-included"

[[answers]]
intent = "portability"
question = "What is not distributed as KAIROS authority?"
target = "s-not-distributed-as-authority"

[[search_contract]]
query = "What is included in the portable KAIROS framework?"
expected = "KAIROS_PORTABILITY_SCOPE#s-included"
required_top_k = 1
+++
# Framework portability scope

## CONTEXT INDEX

- [`s-overview`](#s-overview) — This package is a project-agnostic framework distribution. It must remain separable from
- [`s-included`](#s-included) — | Module | Included material | Reason |
- [`s-not-distributed-as-authority`](#s-not-distributed-as-authority) — The package must not rely on or publish a live customer/project source tree, project-bound
- [`s-portability-acceptance`](#s-portability-acceptance) — Portability is not established by importing the package itself. A release candidate must

<a id="s-overview"></a>
## Overview

> Capsule: This package is a project-agnostic framework distribution. It must remain separable from

This package is a project-agnostic framework distribution. It must remain separable from
any project/customer workspace, transaction history or machine-local authority.

<a id="s-included"></a>
## Included

> Capsule: | Module | Included material | Reason |

| Module | Included material | Reason |
|---|---|---|
| Rules | root `AGENTS.md`, architecture and operating docs | project-neutral authority and procedure |
| Harness | `kairos/kairos_harness/` Python, schemas, templates, tests and docs | reusable context, graph, retrieval, promotion and health implementation |
| Workshop | `workshop/src/runtime_sync_workshop/`, launcher, package metadata and neutral operator docs | reusable fail-closed synchronization machinery |
| Kickstart | `kickstart/`, project-intent prompt, project-intake schema/tests and `docs/PROJECT_KICKSTART.md` | Human-intent-first, compiler-backed initial ground-truth binding |
| Workspace self-model | general methods under `workspace/docs/` | project-neutral documentation only; no local project history |
| Templates | project-neutral configuration templates | portable configuration surface |

<a id="s-not-distributed-as-authority"></a>
## Not distributed as authority

> Capsule: The package must not rely on or publish a live customer/project source tree, project-bound

The package must not rely on or publish a live customer/project source tree, project-bound
intake, local SQLite/WAL/SHM, runtime state, Workshop transaction, lease, seal, build output,
scientific fixture, credential or machine-local path. Derived project databases and dynamic
routers are materialized in the fresh external workspace created for that project.

Only general method sources under `workspace/docs/` are distributed. Consumer architecture,
local goals, tasks, reports and runtime state are excluded. Real use starts with a reviewed
intent contract and a fresh external workspace.

<a id="s-portability-acceptance"></a>
## Portability acceptance

> Capsule: Portability is not established by importing the package itself. A release candidate must

Portability is not established by importing the package itself. A release candidate must
prove all of the following on an external temporary project:

1. reviewed Human intent becomes the active goal/milestone/task before code discovery;
2. compiler records determine translation-unit membership;
3. compiler-order static include closure selects the same project-local headers for the
   admitted literal include cases and fails closed for unsupported dynamic includes;
4. exact source/header Markdown, metadata, database projection and Workshop corpus agree;
5. the first sealed Workshop transaction can patch a translation unit and a header-only
   record through native shadow verification and `POSTCHECK_VERIFIED`;
6. an owner-only include-topology change updates `PROJECT_SOURCE_INDEX.md` and affected
   header-owner metadata in the same transaction;
7. an explicit authority migration can add/remove governed local headers from an isolated candidate,
   verifies the candidate corpus before live mutation, retains removed documents as superseded history,
   and ends in a matching new seal;
8. translation-unit membership or normalized compiler-context migration fails closed with
   `BUILD_AUTHORITY_MIGRATION_REQUIRED` until a project-specific build-authority path can reproduce
   the real build configuration.

Remote publication remains a separate authority decision. No audit or test run in a copy of
this package authorizes a Git or GitHub write to the canonical repository.
