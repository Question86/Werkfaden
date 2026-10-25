# Start with one query

Werkfaden is the public name. The engine in this guide remains KAIROS `v0.1.0-alpha.6`. Commands and identifiers deliberately retain their existing spelling.

## 1. Use the pinned engine

For the initial evaluation use a fresh directory and Python 3.12, matching the release's documented CI setup. Do not overlay the files onto an existing governed project.

```text
git clone --branch v0.1.0-alpha.6 --depth 1 https://github.com/Question86/Werkfaden.git werkfaden
cd werkfaden
python kairos_cli.py search "How do I onboard an arbitrary existing codebase into KAIROS?"
```

The framework query should identify `KAIROS_UNIVERSAL_ONBOARDING#s-cold-onboarding`. That is your first observable result. It reads the bundled framework corpus; it is not a query about a project that has not yet been ingested.

No model-provider API key is required for this query. The CLI searches its packaged knowledge; it does not secretly call an LLM to invent an onboarding answer.

## 2. Establish intent before project work

Ask the framework about the write boundary:

```text
python kairos_cli.py search "After KAIROS seals a project, what is allowed to modify source code or Markdown?"
python -m kickstart prompt --idea "Describe the intended outcome of your private project"
```

`kickstart prompt` produces instructions for an LLM. It does **not** call the model or create an accepted project contract by itself. Give those instructions to your coding agent, review the resulting `kairos-project-kickoff/v1` JSON, and save the reviewed contract separately as `project-spec.json`.

For this first trial choose your own private, non-commercial project. Check the [license](../../LICENSE) before professional, educational or organizational use.

## 3. Detect and intake

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

## 4. Seal, then use Workshop

Only after the intake succeeds:

```text
python workshop/workshop.py --config <fresh-workspace>/.kairos/workshop.config.json seal
```

Existing-file changes use [the normal Workshop lifecycle](../UNIVERSAL_ONBOARDING.md#s-first-workshop-transaction). Static source additions, deletions and renames use [source-set transactions](../UNIVERSAL_ONBOARDING.md#s-source-set-transactions). The agent edits the returned candidate, never the live project or its managed Markdown.

`POSTCHECK_VERIFIED` concerns the governed synchronization checks. It is not evidence that your application is correct or that its tests passed. See [the claim boundary](EVIDENCE.md).

## 5. Report the first point of friction

A valuable first-use report is small: release/commit, operating system, ecosystem, last successful step, sanitized error, and what you expected. [Submit feedback](https://github.com/Question86/Werkfaden/issues/new?template=adoption-feedback.yml). Do not attach your private workspace database, customer source or credentials.

This guide is a human-facing introduction. The [universal operating contract](../UNIVERSAL_ONBOARDING.md) remains procedural authority. Older version-specific pages are not a replacement for the pinned release's newer universal onboarding contract.
