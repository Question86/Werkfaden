# KAIROS 0.1.0-alpha.2 — framework-bootstrap retrieval

This developer preview adds a pre-project knowledge layer: KAIROS can now answer questions about its own operating rules before a project workspace, project goal, runtime state, or project database exists.

## Framework knowledge corpus

Twenty-three core framework Markdown documents now carry `kairos-context/v1` headers, stable section anchors, answer handles, claim boundaries, and bounded search contracts. The admitted set covers the root operating rules and README, core architecture/operations/kickstart/Workshop documents, the harness retrieval and schema specifications, and the Workshop security/operations surfaces.

The Markdown remains authoritative. `kairos/kairos_harness/kairos/framework.db` is a derived read-only SQLite projection bound to those sources by `framework_manifest.json`.

## Pre-project search

From the unpacked release root, no installation is required; the bundled wrapper serves the framework corpus immediately:

```text
python kairos_cli.py search "How do I query KAIROS rules before a project workspace exists?"
python kairos_cli.py search "What owns translation-unit membership?"
python kairos_cli.py search "When is authority migration required?"
```

If the harness is installed, `kairos search "<question>"` is the equivalent console form.

Workspace-free search verifies the packaged framework database hash and, when running from the source release, all admitted framework Markdown hashes before returning section-addressed results. It creates no project state, project database, routing receipt, or retrieval trace.

Once a project exists, `kairos search --workspace <path> ...` retains the existing mutable project-search semantics. Framework and project projections are deliberately separate so framework rules are not copied into or mistaken for project evidence.

## Reproducible framework index

`scripts/build_framework_index.py --verify-determinism` rebuilds the framework projection twice from identical source bytes and requires identical SQLite SHA-256 output. Six bootstrap gold queries must resolve to their declared authoritative sections during the build.

The Markdown structural scanners were also hardened so literal reference and section examples inside fenced or inline code are documentation rather than accidentally interpreted as live KAIROS graph edges or headings.

## Regression status

- KAIROS harness: 145 tests PASS when run as isolated test modules; this includes 6 new framework-bootstrap tests.
- Kickstart: 18/18 PASS.
- Workshop: 21/21 PASS.
- Framework corpus: 23/23 source documents validated and promoted into the immutable projection.
- Framework index: deterministic binary rebuild PASS.
- Bootstrap gold queries: 6/6 PASS.

The existing alpha.1 cold project lifecycle and fail-closed build-authority boundaries remain unchanged; alpha.2 adds the framework bootstrap surface rather than weakening project governance.

## Cold bootstrap validation

The release candidate was unpacked into a fresh directory before publication. Three independent
workspace-free search processes verified the bundled framework projection and routed directly to
the declared framework sections for bootstrap, compiler membership, and authority migration. The
query path created no project database or runtime state; `framework.db` remained the only bundled
SQLite database.

The same cold release then initialized the external CMake project `UCL/CMakeHelloWorld` at the
fixed source commit `12999fb575a6f5b6edda1121f5e9c5069313fad1`, reached a verified intake, zero-issue
Workshop status and seal, rebuilt the executable, and preserved the expected `Hello, World!` output.
Fresh processes then queried framework rules without a workspace and project evidence with the
project workspace; the final Workshop status remained verified with zero issues and a matching seal.

## Scope

This remains a developer preview, not a stable or production-support claim. The project-intake and migration limitations documented in `KNOWN_LIMITATIONS.md` still apply. The framework database is a retrieval projection, not an independent authority and not a substitute for the owning Markdown section returned by search.
