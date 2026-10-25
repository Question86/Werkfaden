# KAIROS 0.1.0-alpha.5

KAIROS 0.1.0-alpha.5 is a post-alpha.4 hardening release. The existing
`v0.1.0-alpha.3` and `v0.1.0-alpha.4` tags remain immutable.

## Changes

- Harden Universal source-set checkout against recursive self-copy when a KAIROS
  workspace or Workshop transaction directory is located below the governed project.
- Persist a recoverable checkout state before candidate copying and provide bounded,
  fail-closed recovery for an orphaned lease created before `state.json` existed.
- Add compiler-observed direct C-family include edges as a separate authority from
  byte ownership and transitive compiled-root reachability.
- Bind the canonical direct-edge set to a topology SHA-256 and carry it through
  intake, ordinary Workshop transactions, authority migrations, shadow verification,
  apply, rollback boundaries and postcheck.
- Project direct include edges into SQLite so KAIROS Search can distinguish direct
  consumers from transitive reachability.
- Keep the human-readable source index bounded: it carries topology digest/count,
  while exact edge rows remain in machine authority and SQLite.
- Preserve compatibility with older framework databases that predate the additive
  compiler include-edge table.

## Pre-seal regression census

- KAIROS harness: 145/145
- Kickstart / Universal Intake: 34/34
- Workshop: 26/26
- Focused include-edge regression: 3/3

The final seal workflow re-runs the release hardening, deterministic framework
rebuild, cold bootstrap and ecosystem matrix on the exact release commit before
creating `v0.1.0-alpha.5`.
