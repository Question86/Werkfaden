+++
schema = "kairos-context/v1"
id = "KAIROS_UNIVERSAL_ONBOARDING"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_UNIVERSAL_ONBOARDING"
updated_at = "2026-09-09T08:40:00Z"
capsule = "Authoritative zero-prior-context onboarding contract for universal KAIROS intake, initial seal, Workshop-only mutation, and source-set transactions."
claim_boundary = "This document defines framework procedure and authority boundaries. It does not prove live project state, successful builds, runtime behavior, or project-specific correctness."
entities = ["KAIROS", "KAIROS_UNIVERSAL_ONBOARDING", "Universal Intake", "Workshop"]
facets = ["bootstrap", "universal-intake", "workshop", "mutation-boundary", "cold-start"]
criteria = []
does_not_answer = ["live project state", "successful application build", "runtime behavior"]

[[answers]]
intent = "onboarding"
question = "How do I onboard an arbitrary existing codebase into KAIROS?"
target = "s-cold-onboarding"

[[answers]]
intent = "universal_intake"
question = "How do detect and --auto work for Python Rust JavaScript TypeScript Go Java .NET Ruby PHP and C++?"
target = "s-universal-intake"

[[answers]]
intent = "mutation_boundary"
question = "After KAIROS seals a project, what is allowed to modify source code or Markdown?"
target = "s-single-mutation-boundary"

[[answers]]
intent = "source_set"
question = "How do I create delete or rename governed source files after seal?"
target = "s-source-set-transactions"

[[answers]]
intent = "clone_boundary"
question = "When must KAIROS clone a project before analysis?"
target = "s-clone-before-risk"

[refs]
rules = "[ref:AGENTS.md#s-fail-closed-boundaries|id:KAIROS_FRAMEWORK_RULES|v:2|rel:constrained_by|tags:rules,mutation|src:framework]"
kickstart = "[ref:docs/PROJECT_KICKSTART.md#s-universal-auto-intake|id:KAIROS_PROJECT_KICKSTART|v:2|rel:implements|tags:kickstart,auto|src:framework]"
workshop = "[ref:docs/WORKSHOP.md#s-universal-source-set-mutation|id:KAIROS_WORKSHOP|v:2|rel:next|tags:workshop,source-set|src:framework]"

[[search_contract]]
query = "How do I onboard an arbitrary existing codebase into KAIROS?"
expected = "KAIROS_UNIVERSAL_ONBOARDING#s-cold-onboarding"
required_top_k = 1

[[search_contract]]
query = "After KAIROS seals a project, what is allowed to modify source code or Markdown?"
expected = "KAIROS_UNIVERSAL_ONBOARDING#s-single-mutation-boundary"
required_top_k = 1

[[search_contract]]
query = "How do I create delete or rename governed source files after seal?"
expected = "KAIROS_UNIVERSAL_ONBOARDING#s-source-set-transactions"
required_top_k = 1
+++
# Universal onboarding and mutation contract

## CONTEXT INDEX

- [`s-cold-onboarding`](#s-cold-onboarding) — The complete zero-prior-context path from framework search to a verified first Workshop transaction.
- [`s-universal-intake`](#s-universal-intake) — Ecosystem detection and normalized initial source authority for static ecosystems plus compiler-backed C-family membership.
- [`s-single-mutation-boundary`](#s-single-mutation-boundary) — After the initial seal, Workshop transactions are the only mutation path for governed source, build/config authority and governed Markdown.
- [`s-clone-before-risk`](#s-clone-before-risk) — Any analysis that could mutate the observed project must run on an isolated clone or snapshot.
- [`s-first-workshop-transaction`](#s-first-workshop-transaction) — Existing-file changes use the normal checkout/prepare/verify/apply lifecycle.
- [`s-source-set-transactions`](#s-source-set-transactions) — Create, delete and rename use Workshop-owned source-set candidate transactions.
- [`s-c-family-boundary`](#s-c-family-boundary) — C/C++/CUDA retains compiler-backed membership and stronger build/header authority boundaries.

<a id="s-cold-onboarding"></a>
## Cold onboarding

> Capsule: A fresh agent must learn the procedure from framework search, establish Human intent, detect the project, run universal intake, seal the verified corpus, and mutate only through Workshop.

From a freshly unpacked KAIROS release, begin with framework search rather than directory inference:

```text
python kairos_cli.py search "How do I onboard an arbitrary existing codebase into KAIROS?"
python kairos_cli.py search "After KAIROS seals a project, what is allowed to modify source code or Markdown?"
```

Then execute the discovered procedure:

```text
python -m kickstart detect <project>
python -m kickstart prompt --idea "<Human intended outcome>"
# review/accept the returned kairos-project-kickoff/v1 contract
python -m kickstart init --auto --project-root <project> --workspace <workspace> --spec-file <contract.json>
python workshop/workshop.py --config <workspace>/.kairos/workshop.config.json seal
```

For a project containing C/C++/CUDA translation units, `--auto` additionally requires compiler-produced membership:

```text
python -m kickstart init --auto --project-root <project> --workspace <workspace> --spec-file <contract.json> --auto-compile-commands <build>/compile_commands.json
```

The successful intake result is `VERIFIED_PENDING_SEAL`; the successful Workshop seal establishes the baseline that every later transaction must match.

<a id="s-universal-intake"></a>
## Universal intake

> Capsule: Static ecosystems are admitted as exact project-local source membership with hashes and ledgers; C-family translation units remain compiler-backed and fail closed without compiler authority.

`kickstart detect <project>` recognizes Python, JavaScript/TypeScript, Rust, Go, Java/Kotlin/Groovy, .NET, Ruby, PHP and C/C++/CUDA markers/source files while excluding generated/vendor trees such as `.venv`, `node_modules`, `target`, build outputs and vendor directories.

For Python/JS/TS/Rust/Go/JVM/.NET/Ruby/PHP, `init --auto` establishes bounded source membership and exact byte/hash/ledger evidence. It does not invent dynamic imports, runtime reachability or build participation. For C/C++/CUDA, translation-unit membership must be present in compiler-produced `compile_commands.json`; project-local headers enter through the compiler-guided include closure. Mixed projects combine these authorities without pretending non-C-family files are translation units.

Initial intake may deterministically create the KAIROS Markdown/database/Workshop baseline from the observed project. It does not modify the governed project source tree.

<a id="s-single-mutation-boundary"></a>
## Single mutation boundary

> Capsule: After seal, no agent, editor, adapter, watcher, repair tool or build helper may directly modify governed source, build/config authority or governed Markdown; only a verified Workshop transaction may promote those changes.

After the initial seal, Workshop is the sole mutation boundary. Create, modify, move, rename and delete operations affecting governed source or governed Markdown must be staged and verified inside Workshop before live application. There is no post-hoc file watcher, rescan-and-repair writer, direct Markdown synchronization path, or agent exception.

A normal agent may edit only transaction candidate/work files. It must never edit the configured live project root or managed Markdown corpus directly.

<a id="s-clone-before-risk"></a>
## Clone before mutation risk

> Capsule: Observation of the original project is read-only; any tool with plausible write side effects runs only on an isolated clone/snapshot.

Initial discovery may read the original project. If a compiler, build system, package manager, generator, formatter, IDE action or other analysis step carries any plausible risk of modifying project/build/config files, first create an isolated clone or snapshot and expose only that clone to the tool. Evidence may be carried back only after verification. The governed original is never used as an experimental work tree.

<a id="s-first-workshop-transaction"></a>
## First Workshop transaction

> Capsule: Existing governed files use the normal transaction lifecycle and reach the live project only after isolated shadow verification.

For an existing governed source file:

```text
python workshop/workshop.py --config <workspace>/.kairos/workshop.config.json checkout --source <path> --purpose "<bounded change>"
# edit only the returned transaction work tree and complete metadata review
python workshop/workshop.py --config <workspace>/.kairos/workshop.config.json prepare TXN_<id>
python workshop/workshop.py --config <workspace>/.kairos/workshop.config.json verify TXN_<id>
python workshop/workshop.py --config <workspace>/.kairos/workshop.config.json apply TXN_<id>
```

`prepare` regenerates exact mechanical Markdown layers. `verify` promotes the candidate documents into an isolated KAIROS shadow. `apply` is allowed only from `SHADOW_VERIFIED`, writes the selected live source and managed Markdown, runs the governed heartbeat, verifies the complete corpus and reseals. Success is `POSTCHECK_VERIFIED` and bit-exact.

<a id="s-source-set-transactions"></a>
## Source-set transactions

> Capsule: Static-ecosystem file creation, deletion and rename are Workshop-owned candidate migrations; direct live file creation followed by rediscovery is forbidden.

For Python/JS/TS/Rust/Go/JVM/.NET/Ruby/PHP source-set changes:

```text
python workshop/workshop.py --config <workspace>/.kairos/workshop.config.json source-set-checkout --purpose "<structural source-set change>"
# create/delete/rename/edit only inside the returned candidate tree
python workshop/workshop.py --config <workspace>/.kairos/workshop.config.json source-set-prepare TXN_<id>
python workshop/workshop.py --config <workspace>/.kairos/workshop.config.json source-set-verify TXN_<id>
python workshop/workshop.py --config <workspace>/.kairos/workshop.config.json source-set-apply TXN_<id>
```

The live project remains unchanged until verified apply. The transaction advances active/tombstoned Markdown, source membership, machine authority and database projection together. Candidate changes to project build/config markers or C-family topology are rejected by this generic static source-set path and require their stronger authority path.

<a id="s-c-family-boundary"></a>
## C-family authority boundary

> Capsule: Universal onboarding does not weaken compiler-backed C-family authority.

C/C++/CUDA translation-unit membership remains compiler-produced. Compiler-guided header topology remains separate from static-ecosystem source-set membership. Changes to C-family compiler context, translation-unit membership or build-system authority are not inferred from file creation and are refused unless an appropriate compiler/build-authority migration can prove and apply the real build transition.
