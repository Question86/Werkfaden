# KAIROS alpha.3 handover

Status captured: 2026-09-09

This document records the current alpha.3 development state and the remaining release gates. It is a handover record, not a claim that every item described below is already present in the published `main` branch.

## Non-negotiable mutation invariant

After initial intake and seal, the original governed project may be changed only by a verified Workshop transaction.

This applies to the entire governed corpus:

- source files
- build files
- project configuration that is part of the governed codebase
- KAIROS Markdown document structure
- renames, moves, creates and deletes

There is no second synchronization path, file-watcher repair path, post-hoc source-set refresh, direct agent edit, adapter write path, or documentation repair path.

The only exception is the initial deterministic creation of the Markdown authority during intake. During that phase the existing build/project state is read-only and is not modified.

If an analysis step or external tool carries any plausible risk of mutating the observed build/project files, KAIROS must first create an isolated clone/snapshot and run that operation only against the clone. The original is never exposed to that mutation risk.

In short:

```text
initial intake: original project = read-only
initial derivation: Markdown / graph / DB are deterministically derived
seal

post-seal mutation: Workshop TX only
```

## Local alpha.3 work reported complete

Universal Intake has been implemented locally but has not yet been published as alpha.3.

`kickstart ... --auto` currently detects and normalizes source authority for:

- Python
- JavaScript / TypeScript
- Rust
- Go
- Java / Kotlin / Groovy
- .NET
- Ruby
- PHP
- C / C++ / CUDA through the existing stricter compiler-backed authority path

Mixed projects are supported, including CMake/C++ + Python, without treating non-C-family files as fake translation units.

The new ecosystem adapters are intended to establish normalized initial source authority with hashes, ecosystem markers and explicit claim boundaries. They must not invent dynamic-import or build-system reality that has not been observed.

C/C++/CUDA remains fail-closed when real compiler-backed authority is unavailable.

## Intended onboarding flow

```text
LLM pulls KAIROS
-> framework search explains onboarding
-> kickstart detect <project>
-> ecosystem(s) detected
-> human intent / project contract
-> kickstart init --auto
-> initial source authority
-> deterministic Markdown authority
-> project DB / graph projection
-> Workshop
-> seal
```

After the initial seal, discovery/intake adapters do not own mutation. New files are not created directly and then rediscovered later. Create / modify / rename / move / delete operations enter the governed project only through Workshop transactions.

## Reported validation already green locally

Kickstart:

- 25/25 tests green
- coverage includes the newly supported ecosystems
- mixed C++ + Python intake
- exclusion of `node_modules`, `.venv`, build trees and similar generated/vendor paths
- C-family fail-closed behavior

Workshop:

- 22/22 tests green
- includes a real Python flow:
  `auto intake -> seal -> app.py edit -> prepare -> shadow verify -> apply -> POSTCHECK_VERIFIED`
- resulting source state verified bit-exact
- existing C++ authority migration tests remain green

These counts describe the reported local development state and must be re-run from the release candidate before publication.

## Remaining alpha.3 work

### 1. Framework documentation and searchable bootstrap contract

Update the canonical framework Markdown so that a fresh LLM can recover the new onboarding behavior through KAIROS search without prior human explanation.

At minimum, the searchable corpus must make the following explicit:

- `detect` and `--auto` are the normal universal-intake entry points
- intake is discovery/derivation, not a continuing mutation mechanism
- the original project is read-only during initial derivation
- post-seal writes are Workshop-TX-only
- Markdown is created deterministically at intake and thereafter advances only inside Workshop transactions
- risky analysis executes on an isolated clone, never on the original
- C-family compiler-backed authority remains stricter and fail-closed

### 2. Audit for forbidden secondary write paths

Search the full corpus and implementation for any code, documentation or workflow that implies or permits a second mutation edge outside Workshop.

Remove or fail-close anything equivalent to:

- direct source edits
- direct build-file edits
- direct Markdown edits after seal
- post-hoc source-set refresh that mutates governed state
- file-watcher synchronization
- repair/sync commands that patch originals outside Workshop
- adapters that write into the governed project after seal
- analysis tools that can mutate originals instead of disposable clones

The release claim should be stronger than "Workshop is preferred": structurally, Workshop must be the only post-seal write path.

### 3. Deterministic framework database rebuild

Rebuild `framework.db` from the updated canonical Markdown/search corpus and verify deterministic output / expected manifest state.

### 4. Full regression gate

Run the complete harness suite currently expected to be 145 tests, plus Python compile checks (`compileall` or the repository's canonical equivalent).

Any count drift must be explained by the actual candidate tree rather than silently accepted.

### 5. Cold release-artifact tests

From a freshly packed and freshly unpacked release artifact, run cold onboarding and mutation tests for at least:

- Python
- JavaScript / TypeScript
- mixed C/C++ + Python

The cold test must prove that a foreign LLM can discover the onboarding contract through framework search and reach a verified Workshop mutation without hidden local state.

### 6. Release consistency

Before tagging/publishing alpha.3:

- regenerate any canonical export/ZIP from the exact candidate tree
- regenerate manifests/seals affected by the candidate
- verify clean working tree
- verify local HEAD == pushed release commit
- record final test counts and artifact hash

## Explicitly not required

Do not add a generic post-seal "source-set refresh" mechanism for Python/JS/etc. merely to discover files created outside KAIROS. Such direct creation would already violate the governing model.

For C/C++/CUDA, external build/compiler authority can affect validation requirements, but it does not create a second mutation path. If `CMakeLists.txt`, Bazel/Meson files or another governed build file must change, that change belongs in the same Workshop transaction as the source change.

## Release-ready definition for alpha.3

Alpha.3 is ready when all of the following are simultaneously true:

1. Universal Intake works from a clean release artifact for the supported ecosystems.
2. A fresh agent can discover the onboarding contract through KAIROS search.
3. Initial derivation leaves the original project untouched.
4. Any mutation-risking analysis uses only an isolated clone.
5. After seal, Workshop is the sole mutation boundary for governed source, build files and Markdown.
6. Full harness and compile gates are green.
7. Cold Python, TS/JS and mixed-project flows are green.
8. Canonical DB/export/manifests are rebuilt from the exact release tree.
9. The pushed commit, release artifact and recorded hashes all refer to the same state.
