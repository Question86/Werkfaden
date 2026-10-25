# Werkfaden

### Keep the thread. Verify the change.

**Project context and controlled changes for AI coding agents.**

An agent can write a convincing patch while working from yesterday's understanding of your project. Werkfaden gives it a different starting point: source-linked Markdown, explicit relationships, a searchable project record, and a verified route for changing governed files.

**[Start with one query](../docs/growth/START_HERE.md)** · **[Inspect the evidence](../docs/growth/EVIDENCE.md)** · **[Read the operating contract](../docs/UNIVERSAL_ONBOARDING.md)**

*By [Yannick Wende](https://github.com/Question86). Formerly presented as KAIROS. The public name changes; the KAIROS engine, CLI and historical release identities do not.*

## What stays together

| You need | The framework provides |
|---|---|
| A current view of the admitted codebase | Source-linked Markdown with exact byte/hash ledgers and a derived SQLite projection |
| A bounded place for the agent to look | Section-addressed search, typed references, project goals and task context |
| A controlled way to change the project | Workshop candidates, shadow verification and postchecks for governed source and Markdown |

Initial intake reads the original project. Analysis with possible write side effects belongs on an isolated copy. After project seal, governed changes must go through Workshop transactions, rather than editing the live project and repairing its documentation afterwards.

These are application-level workflow rules, not an automatically installed operating-system sandbox. [Boundary details](../workshop/SECURITY_BOUNDARY.md).

## Try the smallest useful thing

Download or clone the [pinned engine release](https://github.com/Question86/Werkfaden/tree/v0.1.0-alpha.6), open its directory, and ask the framework how to begin:

```text
python kairos_cli.py search "How do I onboard an arbitrary existing codebase into KAIROS?"
```

No project workspace or model-provider connection is needed for that first framework query. Keep the existing `kairos_cli.py` spelling: this is a public-brand change, not a renamed executable.

The [first-use guide](../docs/growth/START_HERE.md) takes you from that query to reviewed project intent and initial intake. Do not point an agent at your live source tree and tell it to improvise the procedure.

## Languages without invented promises

Python, JavaScript/TypeScript, Rust, Go, Java/Kotlin/Groovy, .NET, Ruby and PHP use bounded static source membership and exact ledgers. That does **not** claim complete import resolution, runtime behavior or build participation.

C/C++/CUDA retains compiler-backed translation-unit membership and compiler-guided include authority. Mixed projects combine the two without treating Python files as C++ translation units. Unsupported or unresolved authority is refused rather than guessed. [Onboarding and scope](../docs/UNIVERSAL_ONBOARDING.md).

## Evidence before adjectives

The pinned engine is `v0.1.0-alpha.6`, commit `13db71aa841c7859e68f5728d0c925ea76343e20`. Its [final-seal run](https://github.com/Question86/Werkfaden/actions/runs/34479342556) records 205 regression tests and a 10-case scripted cold-start matrix.

Those are framework-integrity results, **not** a benchmark proving better model answers, fewer tokens, faster development or bug-free applications. This presentation update is not a new sealed software release. [Claim-by-claim evidence](../docs/growth/EVIDENCE.md).

## Who should try it

Start with a small private, non-commercial project and a coding agent you already use. It is most relevant when session handoffs, stale project context and uncontrolled edits are becoming the problem. This is a command-line developer preview, not a one-click product or an LLM provider.

**Licensing matters:** the [existing license](../LICENSE) permits personal, private, non-commercial use. Professional, organizational, educational, commercial and redistribution uses require Yannick's written permission. This is not an open-source license; the new brand grants no additional rights.

## Show where the thread breaks

Try the first query, then [report your first-use experience](https://github.com/Question86/Werkfaden/issues/new?template=adoption-feedback.yml). A useful report says what you expected, what happened, and where you stopped. Never post private source, credentials or customer data.

Follow the work on [X](https://x.com/yawende86) and [Bluesky](https://bsky.app/profile/ypswe.bsky.social). For technical attribution, cite the pinned engine commit; for the public project name, use **Werkfaden**.
