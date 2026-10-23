+++
schema = "kairos-context/v1"
id = "WERKFADEN_JEV_PORT_VALIDATION"
type = "documentation"
revision = 1
state = "active"
authority = "execution_evidence"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/WERKFADEN_JEV_PORT_VALIDATION"
updated_at = "2026-10-23T23:52:16Z"
capsule = "Executed offline provider tests and native Guardian, metadata, Shadow and postcheck regressions."
claim_boundary = "Mocks verify plumbing only; no real AI review, production Apply or mandatory AI gate was executed."
entities = ["KAIROS", "WERKFADEN_JEV_PORT_VALIDATION"]
facets = ["tests", "jev", "guardian", "metadata"]
criteria = []
does_not_answer = ["AI semantic accuracy", "production readiness"]
[[answers]]
intent = "validation"
question = "Which tests validate the JEV publication port and its limits?"
target = "s-overview"
+++

# Executed validation

## CONTEXT INDEX

- `s-overview` — Commands, outcomes and known boundary failures.

<a id="s-overview"></a>
## Overview

Executed on Windows, Python 3.13, in an isolated clone. The live installation was not used as a test target. Test temporary directories were redirected to the audit directory because the sandbox's default Temp location denied fixture writes.

| Suite / check | Actual result |
| --- | --- |
| `python -m unittest discover -s review/tests -v` | 13 tests, all pass; provider/credential access mocked. Includes the actual attempt entry point reaching the intended adapter with mechanically materialized candidate bytes. |
| `python -m unittest discover -s workshop/tests -p 'test_*.py' -v` | 65 tests executed. First complete run: 62 passed, 3 errors within context-transaction tests. After corrections, all 13 context-transaction tests passed in a targeted rerun. |
| `python -m unittest discover -s kairos/kairos_harness/tests -p 'test_*.py' -v` | 148 tests executed. First complete run: 146 passed, 2 failures. Both failed cases passed in a targeted rerun with the local Git fixture permitted outside the sandbox. |
| `python -m unittest discover -s kickstart/tests -p 'test_*.py' -v` | 36 tests, all pass. |
| `python -m unittest test_framework_bootstrap -v` | 6 tests, all pass after rebuilding the changed framework index. |
| `python scripts/build_framework_index.py --verify-determinism` | Verified regenerated 24-document bundle and gold-query routing; deterministic bytes and logical content. |
| `python scripts/v3_write_boundary_audit.py` | Verified; 74 Python files inspected, no contract failures or write findings. |
| `python -m pip wheel --no-index --no-build-isolation --no-deps ...` | Both Workshop and harness wheels built offline. Extracted-wheel imports of guarded engine, metadata CLI and Guardian search-policy bridge succeeded outside the source checkout. Required quarantined `.txt` sources are included. |

There are 262 distinct unit/integration test cases across the four primary suites, with every initially failing case passing its described targeted rerun. This is not a claim that one final full-suite invocation ran without failures. The original complete logs and targeted rerun logs remain in the local audit directory; productive review packets and local paths were not committed. The branch adds a Linux CI job for the same offline suites; CI completion is separate evidence and is not presumed here.

The Workshop errors were two Windows SQLite cleanup locks (the fixture used a connection context manager without closing the connection) and one old rollback-test patch target after Guardian moved the core behind a restricted loader. The fixture now explicitly closes SELECT-only connections, and fault injection patches the actual Apply function's global dependency. The corrected rollback test still proves restoration of code, metadata and documents. No gate was relaxed.

The harness failures were an MSYS local-clone sandbox denial and a finalization fixture returning BLOCKED during the parallel full run. Both passed unchanged in the two-case rerun. The finalization failure was not diagnosed further; it is not claimed as a fixed production defect.

No real TypeSafe/JEV request was made, no real credential was retrieved, and no live Runtime Apply was performed. Offline tests confirm the actual `model/state/questions` JSON envelope, direct adapter invocation, timeout/error propagation, separate proposition/evidence questions, exact candidate diff, candidate fingerprint changes, context-specific cache, missing-score refusal and existing decision-vector behavior.

Characterization tests deliberately demonstrate remaining gaps: supplied review results can be reused across candidate bytes within the same transaction; invalid numeric scores are not strictly rejected; native Workshop Apply can succeed without a JEV artifact. These tests passing does not mean the missing mandatory AI gate is implemented.

Guardian v2 engine-bound checks, metadata afterchecks, candidate tamper checks, Shadow verification, recovery, rollback and postcheck are covered by the existing/native suites. Guardian-required normal context-document scope remains explicitly unsupported and fails closed; unguarded context transactions and separately proven auxiliary transactions retain their existing contracts.
