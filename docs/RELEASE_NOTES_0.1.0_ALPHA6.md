# KAIROS 0.1.0-alpha.6

Alpha.6 is the corrected sealed release after alpha.5. It preserves the alpha.5 product hardening and direct compiler include-edge authority while fixing the release CI itself.

Included release-integrity fixes:

- Step-2 cold bootstrap verification is release-agnostic and checks against the framework's executable release identity instead of a stale hard-coded alpha.4 literal.
- An already sealed release workflow becomes ineligible on later branch commits instead of attempting to reuse an immutable tag.
- The release candidate is rebuilt and verified before promotion to `v3`; only the exact promoted commit may be tagged.

Product functionality retained from alpha.5:

- nested KAIROS workspace pruning during static source-set checkout;
- recoverable checkout staging and bounded orphan-lease recovery;
- direct compiler-observed C-family include-edge authority with topology digest;
- SQLite/Search projection of direct include edges;
- Workshop and AuthorityMigration propagation of include-edge authority;
- fail-closed postcheck/tamper validation.

FINAL_SEAL_READY_ALPHA6: true
