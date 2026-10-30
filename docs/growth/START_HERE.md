+++
schema = "kairos-context/v1"
id = "KAIROS_PUBLIC_START_HERE"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_PUBLIC_START_HERE"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Human first-use route: framework query, reviewed intent, isolated project detection/intake, initial seal and governed Workshop changes."
claim_boundary = "Navigation to current operating contracts. This guide does not initialize a project without reviewed Human intent or prove current preview CI, release or project correctness."
entities = ["KAIROS", "KAIROS_PUBLIC_START_HERE"]
facets = ["entry", "onboarding", "intent", "fresh-workspace"]
criteria = []
does_not_answer = ["live project state", "current acceptance without verified evidence"]
distribution_role = "navigation"

[[answers]]
intent = "procedure"
question = "How does a new user start with Werkfaden and a fresh project workspace?"
target = "s-1-use-the-current-framework-preview"

[[answers]]
intent = "procedure"
question = "What must be reviewed before first project intake?"
target = "s-2-establish-intent-before-project-work"

[refs]
onboarding = "[ref:docs/UNIVERSAL_ONBOARDING.md#s-cold-onboarding|id:KAIROS_UNIVERSAL_ONBOARDING|v:dynamic|rel:requires|tags:onboarding,intent|src:framework]"
kickstart = "[ref:docs/PROJECT_KICKSTART.md#s-1-project-intent-precedes-code-discovery|id:KAIROS_PROJECT_KICKSTART|v:dynamic|rel:requires|tags:kickstart,human-intent|src:framework]"

[[search_contract]]
query = "How does a new user start with Werkfaden and a fresh project workspace?"
expected = "KAIROS_PUBLIC_START_HERE#s-1-use-the-current-framework-preview"
required_top_k = 5

[[search_contract]]
query = "What must be reviewed before first project intake?"
expected = "KAIROS_PUBLIC_START_HERE#s-2-establish-intent-before-project-work"
required_top_k = 5
+++
# Start with one query

## CONTEXT INDEX

- [1. Use the current framework preview](#s-1-use-the-current-framework-preview)
- [2. Establish intent before project work](#s-2-establish-intent-before-project-work)
- [3. Detect and intake](#s-3-detect-and-intake)
- [4. Seal, then use Workshop](#s-4-seal-then-use-workshop)
- [5. Report the first point of friction](#s-5-report-the-first-point-of-friction)


Werkfaden is the public name; the engine and commands retain KAIROS spelling. This entry follows the current project-neutral-framework developer preview. Historical release records are in the indexed release archive; they do not prove current readiness.

<a id="s-1-use-the-current-framework-preview"></a>
## 1. Use the current framework preview

> Capsule: Start in a fresh framework checkout and ask its immutable corpus before project work.

For the initial evaluation use a fresh directory and Python 3.12, matching the current CI interpreter; the harness declares Python3.11or newer. Do not overlay the files onto an existing governed project.

```text
git clone --branch project-neutral-framework --single-branch --depth 1 https://github.com/Question86/Werkfaden.git werkfaden
cd werkfaden
python kairos_cli.py search "How do I onboard an arbitrary existing codebase into KAIROS?"
```

The framework query should identify `KAIROS_UNIVERSAL_ONBOARDING#s-cold-onboarding`. That is your first observable result. It reads the bundled framework corpus; it is not a query about a project that has not yet been ingested.

No model-provider API key is required for this query. The CLI searches its packaged knowledge; it does not secretly call an LLM to invent an onboarding answer.

<a id="s-2-establish-intent-before-project-work"></a>
## 2. Establish intent before project work

> Capsule: Render an LLM prompt and review the resulting Human goal/milestone/task contract.

Ask the framework about the write boundary:

```text
python kairos_cli.py search "After KAIROS seals a project, what is allowed to modify source code or Markdown?"
python -m kickstart prompt --idea "Describe the intended outcome of your private project"
```

`kickstart prompt` produces instructions for an LLM. It does **not** call the model or create an accepted project contract by itself. Give those instructions to your coding agent, review the resulting `kairos-project-kickoff/v1` JSON, and save the reviewed contract separately as `project-spec.json`.

For this first trial choose your own private, non-commercial project. Check the [license](../../LICENSE) before professional, educational or organizational use.

<a id="s-3-detect-and-intake"></a>
## 3. Detect and intake

> Capsule: Observe the intended project read-only and initialize a fresh explicitly selected workspace.

Substitute your paths. The workspace must be fresh. A sibling directory outside the project makes the separation easy to see.

```text
python -m kickstart detect <project>
python -m kickstart init --auto --project-root <project> --workspace <fresh-workspace> --spec-file project-spec.json
```

C/C++/CUDA or mixed projects containing those translation units additionally require the real compiler database:

```text
python -m kickstart init --auto --project-root <project> --workspace <fresh-workspace> --spec-file project-spec.json --auto-compile-commands <build>/compile_commands.json
```

Do not invent compiler records or run potentially mutating build discovery on the original. Use the [clone-before-risk contract](../UNIVERSAL_ONBOARDING.md#s-clone-before-risk).

Expected successful intake state: `VERIFIED_PENDING_SEAL`. If a command refuses the project, preserve the diagnostic and stop; do not edit configuration until it turns green.

<a id="s-4-seal-then-use-workshop"></a>
## 4. Seal, then use Workshop

> Capsule: Seal a verified baseline and edit only supported Workshop candidates.

Only after the intake succeeds:

```text
python workshop/workshop.py --config <fresh-workspace>/.kairos/workshop.config.json seal
```

Existing-file changes use [the normal Workshop lifecycle](../UNIVERSAL_ONBOARDING.md#s-first-workshop-transaction). Static source additions, deletions and renames use [source-set transactions](../UNIVERSAL_ONBOARDING.md#s-source-set-transactions). The agent edits the returned candidate, never the live project or its managed Markdown.

`POSTCHECK_VERIFIED` concerns the governed synchronization checks. It is not evidence that your application is correct or that its tests passed. See [the claim boundary](EVIDENCE.md).

<a id="s-5-report-the-first-point-of-friction"></a>
## 5. Report the first point of friction

> Capsule: Report sanitized reproducible friction without attaching private workspace data.

A valuable first-use report is small: release/commit, operating system, ecosystem, last successful step, sanitized error, and what you expected. [Submit feedback](https://github.com/Question86/Werkfaden/issues/new?template=adoption-feedback.yml). Do not attach your private workspace database, customer source or credentials.

This guide is a human-facing introduction. The [universal operating contract](../UNIVERSAL_ONBOARDING.md) remains procedural authority. Older version-specific pages are not a replacement for the current universal onboarding contract.
