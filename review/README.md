+++
schema = "kairos-context/v1"
id = "WERKFADEN_JEV_REVIEW"
type = "documentation"
revision = 2
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/WERKFADEN_JEV_REVIEW"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Operate the standalone JEV submission workflow and understand its authority limits."
claim_boundary = "These instructions do not establish a mandatory AI gate or project approval."
entities = ["KAIROS", "WERKFADEN_JEV_REVIEW"]
facets = ["jev", "ai-review", "limitations"]
criteria = []
does_not_answer = ["live project approval", "runtime correctness"]
distribution_role = "current_authority"

[[answers]]
intent = "ai_review"
question = "Operate the standalone JEV submission workflow and understand its authority limits."
target = "s-overview"

[[search_contract]]
query = "Operate the standalone JEV submission workflow and understand its authority limits."
expected = "WERKFADEN_JEV_REVIEW#s-overview"
required_top_k = 5

[refs]
+++
# JEV review tools

## CONTEXT INDEX

- [Overview](#s-overview)

<a id="s-overview"></a>
## Overview

These are the existing local submission and diagnostic tools, ported without private project policy. They are optional standalone review tools. **They do not enforce AI approval at Workshop Apply.** Read [the comparison](COMPARISON.md) before using a score as decision evidence.

Requirements: Python 3.11+, standard library, and Windows Credential Manager for the deployed transport's credential resolution. No provider SDK is required. The fixed endpoint is `https://api.typesafe.ai/v1/systemone`; the current alias is `jev-latest`. `JEV_API_URL`, `OPENROUTER_API_KEY` and arbitrary secret-file variables do not configure this transport.

1. Store the key locally using `python review/jev_configure_credential.py` (hidden input). `JEV_CREDENTIAL_TARGET` optionally selects another Windows Credential Manager target. Never commit the key.
2. Supply an explicitly reviewed project question profile. `questions.example.json` illustrates the actual `name -> {type: noul, instructions: ...}` shape; it is not a complete project review policy.
3. Use a real existing Workshop transaction with existing `runtime/` / `tests/` source counterparts and pass its authoritative configuration explicitly.

```powershell
$env:JEV_QUESTIONS_FILE = (Resolve-Path 'review/questions.example.json').Path
$env:JEV_RETRY_ATTEMPTS = '1'
python review/jev_workshop_attempt_gate_typesafe.py TXN_example --workshop-root '<workshop-root>' --config '<canonical-workshop-config>' --out '<private-output>/attempt.json' --include-state
python review/jev_workshop_attempt_workflow.py TXN_example --workshop-root '<workshop-root>' --config '<canonical-workshop-config>' --require-positive mechanism_preserved --reject-positive unsupported_assumption_present --attempt-out '<private-output>/attempt.json' --out '<private-output>/workflow.json' --no-tomography
```

The commands above send candidate source content to the provider when actually executed. Review the exact disclosure and obtain any project-required authorization first. Output artifacts belong outside the repository: they can contain private source, local paths, current state and responses. The attempt call prints JSON even for low scores; use the workflow's exit code and recorded decision for standalone routing, subject to the known binding limitations.

For optional decomposition use `python review/jev_adaptive_patch_tree.py --help`. It sends separate support and evidence-sufficiency questions and can invoke governed KAIROS retrieval. Run it against an authorized isolated/project workspace. Default workflow limits are bounded and differ from standalone tree limits. `--previous-workflow` plus a revision note implements the existing low-review revision route; an approved receipt never authorizes direct Apply.

The public port requires explicit `--require-positive` and `--reject-positive` settings and an explicit question profile. Private local defaults were omitted, not replaced by a weaker default. Threshold comparison, scoring and retry logic are preserved. See [asking rules](HOW_TO_ASK_JEV.md) and [tomography method](ADAPTIVE_PATCH_TOMOGRAPHY.md); those procedures state desired evidence discipline and do not prove the scripts implement every stated rule.

Run offline tests with `python -m unittest discover -s review/tests -v`. The tests mock provider traffic and do not retrieve a key. See [validation](VALIDATION.md) and [comparison](COMPARISON.md) for remaining gaps.
