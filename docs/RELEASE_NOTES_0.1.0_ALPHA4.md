# KAIROS 0.1.0-alpha.4 - Windows portability and verification hardening

KAIROS 0.1.0-alpha.4 is the post-alpha.3 hardening release for portable,
relocatable checkouts and fail-closed Windows path handling.

## Hardening scope

- Test and bootstrap temporary state is created outside the source checkout.
- Generated Workshop configuration uses relocatable, config-relative paths.
- Path escape, drive-relative paths, symbolic links, junctions, and other
  reparse points are rejected at governed boundaries.
- CMake configuration runs against a disposable source clone and refuses
  project-local wrappers or build directories inside the governed project.
- Framework rebuild verification has a platform-independent logical content
  digest in addition to the bundled database byte digest.
- Relocation, config escape, and Windows path regressions are covered by tests.

## Regression census

- KAIROS harness: 145 tests
- Kickstart: 32 tests
- Workshop: 23 tests
- Total: 200 tests

The `v0.1.0-alpha.3` tag remains immutable. The alpha.4 workflow seals and tags
only the exact post-hardening commit after all gates pass.
