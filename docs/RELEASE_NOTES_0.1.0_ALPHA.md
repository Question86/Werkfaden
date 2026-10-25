# KAIROS 0.1.0-alpha.1 — developer preview

This release is the first public developer-preview candidate validated from a cold distribution
rather than from the development workspace that produced it.

## Frozen input

- Pre-release RC archive SHA-256: `799c4f31c073582b082c8571f6398bf3c6db3fb8fa6b2ed42278e36311ca0e84`
- Pre-release RC archive size: 415382 bytes
- Files in frozen RC: 132

## Cold reproducibility validation

The frozen RC was unpacked into a fresh directory with no inherited KAIROS runtime state. The
external project fixture was reconstructed from the public GitHub repository
`UCL/CMakeHelloWorld` at commit `12999fb575a6f5b6edda1121f5e9c5069313fad1`.

The validation sequence was:

1. generate/review a project-intent contract;
2. compiler-backed CMake intake into a fresh external KAIROS workspace;
3. require intake, corpus and full health verification;
4. seal the initial corpus;
5. perform a normal source transaction through `prepare -> verify -> apply`;
6. rebuild/run the external project;
7. stage an isolated candidate that adds one governed local header and changes the source;
8. complete `authority-checkout -> authority-prepare -> authority-verify -> authority-apply`;
9. start fresh Python processes with no retained in-memory Workshop/KAIROS objects;
10. verify status, seal match, full health and question-oriented retrieval from persisted state;
11. perform another post-restart Workshop transaction;
12. rebuild/run the project and require final corpus verification.

Observed final state:

- project intake: `verified=true`;
- initial and final Workshop status: verified;
- normal source apply: `POSTCHECK_VERIFIED`, `bit_exact=true`;
- authority migration apply: `POSTCHECK_VERIFIED`, `bit_exact=true`;
- post-restart continuation apply: `POSTCHECK_VERIFIED`, `bit_exact=true`;
- final corpus issues: `0`;
- final seal: matches current package;
- final lease: none;
- full KAIROS health: `PASS`;
- rebuilt external executable: successful, expected output retained.

The upstream fixture's minimal CMake file emits a developer warning because it calls `project()`
before `cmake_minimum_required()`. That warning belongs to the external fixture and did not affect
configure/build success or KAIROS verification.

## Regression suites on the frozen code

- KAIROS harness: 139/139 PASS
- Kickstart: 18/18 PASS
- Workshop: 21/21 PASS
- Python compilation: PASS

## Major alpha capabilities

- Human-intent-first project contracts before source discovery.
- Compiler-backed translation-unit membership.
- Per-translation-unit include context with conservative external/include handling.
- Exact source/header ledgers and hash-bound Markdown authority.
- Same-heartbeat SQLite projection.
- Sealed, fail-closed Workshop transactions with isolated shadow verification.
- Synchronized owner-only include-topology updates.
- Explicit local-header authority migration with superseded history.
- Cross-binding checks between project intake, Workshop configuration, compiler context,
  Markdown and database state.
- Refusal of generic build-authority changes that cannot be proven from the real build system.

See `docs/KNOWN_LIMITATIONS.md` before using the release on a new project.
