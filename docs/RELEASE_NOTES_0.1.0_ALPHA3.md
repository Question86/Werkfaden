# KAIROS 0.1.0-alpha.3 — universal intake and Workshop-bound development

KAIROS 0.1.0-alpha.3 extends the project-intake and synchronized-development model beyond the original C/C++-centric path while preserving the same fail-closed authority boundary.

The release remains a developer preview. It proves the synchronization and bootstrap properties described below; it does not claim application-level correctness, successful production builds for arbitrary projects, or production-support status.

## Universal project intake

A fresh project can now be inspected with:

```text
python -m kickstart detect <project>
```

and initialized with:

```text
python -m kickstart init --auto --project-root <project> --workspace <workspace> --spec-file <contract.json>
```

The universal intake recognizes and materializes exact source authority for:

- Python;
- JavaScript / TypeScript;
- Rust;
- Go;
- Java / Kotlin / Groovy;
- .NET languages;
- Ruby;
- PHP;
- C / C++ / CUDA through the existing compiler-backed authority path.

Static ecosystems are admitted as exact project-local source membership with byte counts, SHA-256 hashes and verbatim line ledgers. KAIROS does not promote filename discovery into invented dynamic-import, runtime-reachability or build-participation claims.

C/C++/CUDA remains stricter. `--auto` requires compiler-produced `compile_commands.json` for C-family translation-unit membership, and project-local header authority remains compiler-context guided and fail-closed.

Mixed projects combine those authority classes without pretending non-C-family files are compiler translation units.

## Immutable intake boundary

Initial intake observes the governed project read-only and deterministically derives the initial KAIROS Markdown, database projection and Workshop machine authority.

Any tool with plausible project-write side effects must operate on an isolated clone or snapshot. The legacy CMake intake path is hardened accordingly: CMake now configures only an isolated source clone, rejects build directories inside the governed project, and remaps compiler evidence back to the original observed source identities before intake consumes it.

A regression test deliberately uses a CMake configure script that writes into `CMAKE_SOURCE_DIR`; the write reaches only the disposable clone and the governed project remains byte-identical.

## One post-seal mutation boundary

After seal, Workshop is the sole mutation boundary for governed source and governed Markdown.

Existing governed files use:

```text
checkout -> prepare -> verify -> apply -> POSTCHECK_VERIFIED
```

Candidate edits live in the transaction work tree. `prepare` regenerates exact mechanical Markdown evidence, `verify` promotes into an isolated KAIROS shadow, and only a verified `apply` may change the live governed state.

Static-ecosystem source-set changes use the Workshop-owned structural path:

```text
source-set-checkout -> source-set-prepare -> source-set-verify -> source-set-apply
```

This path supports create, delete and rename while advancing source membership, active/tombstoned Markdown, machine authority, KAIROS projection and the seal as one verified transition. Direct live creation followed by rediscovery is not an authority path.

C-family header/compiler/build topology retains its stronger migration boundaries; universal intake does not weaken compiler authority.

## Self-describing framework bootstrap

The immutable framework corpus now includes `KAIROS_UNIVERSAL_ONBOARDING`. A zero-context agent can query the bundled framework search before any project workspace exists and recover the universal onboarding and mutation contracts instead of reconstructing them from repository layout.

The framework bundle is versioned `0.1.0-alpha.3` and contains:

- 24 canonical framework documents;
- 9 framework gold queries;
- deterministic `framework.db` SHA-256 `22d79a1a0e12ad346d67d71339677fbc39bd0c55d29bfa0b8b52ccd1819e01f5`.

An independent rebuild from the canonical Markdown produces a byte-identical database.

## Cold bootstrap proof

From a fresh `git archive` with no `.git` metadata, the release candidate completes:

```text
framework search
-> detect
-> init --auto
-> VERIFIED_PENDING_SEAL
-> seal
-> checkout
-> prepare
-> SHADOW_VERIFIED
-> apply
-> POSTCHECK_VERIFIED
```

The cold gate verifies that framework search works from the packaged immutable projection and that `detect`, initial intake, `prepare` and shadow `verify` leave the governed live project unchanged. The final verified apply is bit-exact.

## Cross-ecosystem release matrix

The final hardening matrix runs from a fresh release-style archive. Ten cases reach `POSTCHECK_VERIFIED` with bit-exact apply:

1. Python;
2. JavaScript / TypeScript;
3. Rust;
4. Go;
5. JVM / Java;
6. .NET / C#;
7. Ruby;
8. PHP;
9. mixed C-family + Python with compiler-backed C-family membership;
10. Python structural source-set create/delete/rename.

## Final regression gate

The Step-3 release-hardening gate passes:

- KAIROS harness: 145/145;
- Kickstart / Universal Intake: 31/31;
- Workshop: 21/21;
- Python `compileall`: PASS;
- sole-write-boundary audit: PASS across 56 productive Python implementation files, zero findings;
- deterministic framework DB rebuild: PASS;
- zero-state cold bootstrap: PASS;
- cold ecosystem matrix: 10/10 PASS.

## Release seal

The final release workflow repeats the hardening gates against one exact Git commit, builds the source ZIP twice with `git archive` and requires byte identity, then emits a content-addressed seal manifest containing the exact commit SHA, release ZIP SHA-256, framework DB SHA-256 and regression counts.

Only after those checks pass is the exact commit tagged `v0.1.0-alpha.3`. The release ZIP and seal manifest are retained together as GitHub Actions artifacts, so the seal does not require a self-referential generated file to be committed back into the source tree.
