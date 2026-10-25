# KAIROS v3 worklog

Status established: 2026-09-09

## Development authority

The `v3` branch is the sole development authority for the alpha.3 / universal-intake rebuild.

Rules:

1. No unpublished local working tree is treated as authoritative.
2. Every functional change is committed to `v3` before the next development step continues.
3. GitHub branch state is the recoverable handover state at all times.
4. Local/container execution may be used only as a disposable verification environment when necessary; no unique code or design state may exist only there.
5. If work is interrupted, continuation starts from the latest `v3` commit, not from chat memory or an unpushed filesystem.

## Non-negotiable system invariant

Initial intake observes the original project read-only and deterministically derives KAIROS Markdown / graph / database authority.

After seal, the only permitted mutation path for governed source, build files, project configuration, and governed Markdown is a verified Workshop transaction.

Any analysis or external tool with a plausible mutation risk must run against an isolated clone/snapshot, never the governed original.

## Three-step seal plan

1. Universal Workshop Integration.
2. Framework Contract + Search + Cold Bootstrap.
3. Release Hardening + Final Gate.

No fourth development block is planned before final seal.

## Step 1 — COMPLETE

Universal Workshop Integration is closed on `v3`.

Implemented:

- universal initial source intake for Python, JavaScript/TypeScript, Rust, Go, Java/Kotlin/Groovy, .NET, Ruby and PHP;
- compiler-backed fail-closed C/C++/CUDA translation-unit authority and compiler-backed local-header closure;
- mixed projects, including C/C++ plus static-language sources, without promoting non-C files into compiler translation units;
- exact per-file Markdown mirrors with byte count, SHA-256, line ledger and explicit claim boundary;
- one Workshop corpus covering universal source membership, managed Markdown, external blueprint mirrors and KAIROS database projection;
- ordinary post-seal source edits for universal ecosystems through the native `checkout -> prepare -> verify -> apply -> POSTCHECK_VERIFIED` path;
- C-family preprocessor/include analysis restricted to C-family sources only;
- C-family semantic token checks retained, while static ecosystems use exact mechanical byte/ledger authority without applying a fake C++ lexer;
- Workshop-owned `source-set-*` transactions for static source create/delete/rename plus related static-source edits;
- source-set candidate state lives inside the Workshop transaction directory and the governed live project remains unchanged through prepare and shadow verification;
- source-set apply updates source membership, active/tombstoned Markdown, machine authority, KAIROS projection and seal as one verified transition;
- build/config mutations and C-family topology changes fail closed in the static source-set transaction rather than acquiring an unauthorized second write path.

Step-1 gates proven on GitHub Actions:

- Python ordinary Workshop roundtrip -> `POSTCHECK_VERIFIED`;
- TypeScript ordinary Workshop roundtrip -> `POSTCHECK_VERIFIED`;
- Rust ordinary Workshop roundtrip -> `POSTCHECK_VERIFIED`;
- mixed C++ + Python ordinary Workshop roundtrip -> `POSTCHECK_VERIFIED` with compiler-backed C header authority retained;
- Python source-set create + delete + rename + existing-file edit -> shadow verified, live project unchanged before apply, then `POSTCHECK_VERIFIED`;
- attempted build/config mutation inside static source-set candidate -> fail closed, live project unchanged;
- full Kickstart gate green;
- full existing Workshop regression gate green;
- Python `compileall` gate green.

The source-set transaction deliberately does not claim generic build-system mutation authority. If a topology change requires CMake/Cargo/Gradle/package/build configuration to change, that configuration must have an explicit Workshop authority path; direct live mutation remains forbidden.

## Step 2 — COMPLETE

Framework Contract + Search + Cold Bootstrap is closed on `v3`.

Implemented:

- canonical `KAIROS_UNIVERSAL_ONBOARDING` operating contract admitted to the immutable framework corpus;
- framework-search answers for zero-context onboarding, universal `detect` / `--auto`, the post-seal single mutation boundary, clone-before-risk, ordinary Workshop edits and static source-set transactions;
- `AGENTS.md`, framework bootstrap, project kickstart and Workshop contracts advanced to the universal-intake model so the searchable corpus no longer defaults to a C++-only onboarding story;
- explicit static-ecosystem claim boundaries: project-local membership/hashes/ledgers without invented imports, runtime reachability or build participation;
- explicit compiler-backed fail-closed C/C++/CUDA boundary retained for mixed projects;
- explicit rule that potentially mutating analysis/build tooling runs only against an isolated clone/snapshot;
- framework bundle version advanced to `0.1.0-alpha.3` and `docs/UNIVERSAL_ONBOARDING.md` added to the executable framework document inventory;
- deterministic framework search projection rebuilt from 24 canonical framework documents with 9 gold queries;
- universal intake public result made explicit with top-level `verified = true` after heartbeat and Workshop corpus verification;
- zero-state cold-bootstrap gate implemented using only public source-release CLI surfaces.

Step-2 gates proven on GitHub Actions:

- bundled framework manifest/database/source inventory hash verification passes;
- independent framework rebuild is binary-identical to the bundled `framework.db`;
- framework database SHA-256 is `22d79a1a0e12ad346d67d71339677fbc39bd0c55d29bfa0b8b52ccd1819e01f5`;
- framework search returns `KAIROS_UNIVERSAL_ONBOARDING#s-cold-onboarding` for arbitrary-codebase onboarding;
- framework search returns `KAIROS_UNIVERSAL_ONBOARDING#s-single-mutation-boundary` for post-seal write authority;
- framework search returns `KAIROS_UNIVERSAL_ONBOARDING#s-source-set-transactions` for create/delete/rename authority;
- a fresh `git archive` with no `.git` metadata serves the verified immutable framework search bundle;
- from that fresh archive a zero-state Python project completes `detect -> init --auto -> VERIFIED_PENDING_SEAL -> seal -> checkout -> prepare -> SHADOW_VERIFIED -> apply -> POSTCHECK_VERIFIED`;
- the cold gate verifies that `detect`, initial intake, `prepare` and shadow `verify` leave the governed live project byte-identical until verified apply;
- final Workshop apply is bit-exact.

## Step 3 — COMPLETE / FINAL SEAL CANDIDATE

Release Hardening + Final Gate is closed as a development block. No new feature work remains before the alpha.3 seal.

Hardening completed:

- the legacy CMake intake path no longer exposes the governed source directory to CMake; configure runs against a disposable source clone;
- CMake build directories inside the governed project are rejected before configure;
- compiler evidence emitted from the clone is normalized back to the original observed source identities before authority intake;
- an adversarial regression deliberately writes into `CMAKE_SOURCE_DIR` during configure and proves the write reaches only the disposable clone;
- a machine-readable AST write-boundary audit scans productive KAIROS/Kickstart/Workshop Python implementation paths and fails if a live source/runtime/blueprint authority root reaches a write sink outside the Workshop implementation boundary;
- clone-isolated mutation-capable tool calls are separately bound to explicit static contracts and regression tests rather than treated as generic write exceptions;
- a cold release-style cross-ecosystem matrix exercises the public CLI and Workshop surfaces from a fresh archive with no `.git` state;
- `docs/RELEASE_NOTES_0.1.0_ALPHA3.md` records the alpha.3 scope and verified release claims;
- `.github/workflows/v3-final-seal.yml` repeats all release gates on the exact final commit, builds the source ZIP twice, requires byte identity, emits a content-addressed seal manifest and tags only after every gate succeeds.

Step-3 hardening gate proven on GitHub Actions before arming the final seal:

- Python `compileall`: PASS;
- sole post-seal write-boundary audit: PASS, 56 productive Python implementation files scanned, zero findings;
- KAIROS harness: 145/145 PASS;
- Kickstart / Universal Intake: 31/31 PASS, including clone-isolated CMake regression;
- Workshop: 21/21 PASS;
- deterministic framework projection rebuild: PASS and byte-identical to bundled `framework.db`;
- framework database SHA-256 remains `22d79a1a0e12ad346d67d71339677fbc39bd0c55d29bfa0b8b52ccd1819e01f5`;
- zero-state cold bootstrap from fresh `git archive`: PASS, first transaction `POSTCHECK_VERIFIED`, bit-exact;
- cold ecosystem matrix: 10/10 PASS;
- cold cases reaching bit-exact `POSTCHECK_VERIFIED`: Python, JavaScript/TypeScript, Rust, Go, JVM/Java, .NET/C#, Ruby, PHP, mixed C-family + Python, and Python source-set create/delete/rename.

The final seal workflow must now rerun these gates against this exact armed commit. It may tag only that exact commit as `v0.1.0-alpha.3` and must retain the deterministic source ZIP plus `kairos-release-seal/v1` manifest together as GitHub Actions artifacts.

FINAL_SEAL_READY: true

After the successful seal workflow there is no fourth development block. Any later change is post-alpha.3 work and requires a new version/branch decision rather than silently modifying the sealed alpha.3 commit.

## Post-alpha.3 hardening - alpha.4 candidate

The Windows portability and verification changes are versioned as
`0.1.0-alpha.4`; the immutable `v0.1.0-alpha.3` tag remains on its original
sealed commit. The alpha.4 final-seal workflow requires 145 KAIROS harness,
32 Kickstart, and 23 Workshop tests, then verifies the combined census of 200
tests before creating the content-addressed artifact pair and exact alpha.4 tag.

FINAL_SEAL_READY_ALPHA4: true

## Post-alpha.4 hardening - alpha.5 candidate

`0.1.0-alpha.5` is the separate post-alpha.4 hardening release. The immutable
`v0.1.0-alpha.3` and `v0.1.0-alpha.4` tags remain on their original sealed commits.

Alpha.5 closes two independently observed authority gaps without changing the
single post-seal mutation boundary:

- Universal source-set checkout excludes KAIROS/Workshop state roots, persists a
  recoverable `CHECKOUT_STAGING` state before candidate copying, and provides a
  bounded fail-closed orphan-lease recovery when live package and seal identity
  are still exact.
- C-family intake and Workshop transactions persist exact direct compiler include
  edges separately from transitive compiled-root reachability and byte ownership.
  The edge set has a canonical topology SHA-256, is projected into SQLite for
  Search, and is verified through normal edit and authority-migration shadow/
  postcheck paths.

Pre-seal alpha.5 gates proven on GitHub Actions:

- direct include-edge focused regression: 3/3 PASS;
- KAIROS harness: 145/145 PASS;
- Kickstart / Universal Intake: 34/34 PASS;
- Workshop: 26/26 PASS;
- deterministic alpha.5 framework projection rebuild: PASS;
- source-set recursion and orphan-recovery regressions: PASS;
- authority-migration include-edge transport: PASS.

The final alpha.5 seal workflow must rerun the release hardening, regression,
framework determinism and cold-bootstrap/ecosystem gates against the exact `v3`
commit before creating the deterministic source ZIP, seal manifest and tag.

FINAL_SEAL_READY_ALPHA5: true
