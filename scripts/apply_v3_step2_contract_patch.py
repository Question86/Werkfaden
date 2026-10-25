from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = "2026-09-09T08:45:00Z"


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one match in {path}, found {count}: {old[:90]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_before(path: str, marker: str, addition: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(marker)
    if count != 1:
        raise SystemExit(f"expected exactly one insertion marker in {path}, found {count}")
    target.write_text(text.replace(marker, addition + marker, 1), encoding="utf-8")


# AGENTS.md: make the top-level operating contract universal rather than C++-default.
replace_once("AGENTS.md", 'revision = 1\n', 'revision = 2\n')
replace_once("AGENTS.md", 'updated_at = "2026-09-08T10:30:00Z"', f'updated_at = "{STAMP}"')
replace_once(
    "AGENTS.md",
    '''For a fresh external project, Human intent comes first. A reviewed
`kairos-project-kickoff/v1` contract establishes the project goal, milestone, task and
criteria before compiler discovery. The compiler then owns translation-unit membership;
KAIROS may not infer project goals from source layout or replace compiler membership with
filename/build-prose guesses. Static header closure must preserve compiler include-root
order and fail closed when a dependency cannot be represented without preprocessor guessing.
''',
    '''For a fresh external project, Human intent comes first. A reviewed
`kairos-project-kickoff/v1` contract establishes the project goal, milestone, task and
criteria before source discovery. The agent then runs `kickstart detect <project>` and
`kickstart init --auto`. Python/JavaScript/TypeScript/Rust/Go/JVM/.NET/Ruby/PHP use bounded
project-local source membership with exact hashes and ledgers; KAIROS does not invent dynamic
imports, runtime reachability or build participation. C/C++/CUDA translation-unit membership
remains compiler-produced and fail-closed, with compiler-guided local-header closure. Mixed
projects combine these authorities without projecting non-C-family files as translation units.
''',
)
replace_once(
    "AGENTS.md",
    '''The harness owns document validation and deterministic promotion. The Workshop is the only
writer for a configured live Runtime/blueprint corpus, and only after a sealed checkout,
metadata review, mechanical preparation, isolated verification, and postcheck. Humans,
agents, editors, build tools, and copy utilities do not edit a configured live corpus.
''',
    '''The harness owns document validation and deterministic promotion. Initial intake observes the
original project read-only and may deterministically create the first KAIROS Markdown/database
baseline. After seal, the Workshop is the only writer for governed source, governed build/config
authority and governed Markdown. Humans, agents, editors, watchers, adapters, build tools and copy
utilities do not edit the configured live corpus directly. They may edit only Workshop transaction
candidate/work trees. Any analysis or external tool with plausible write side effects must run on
an isolated clone/snapshot rather than the governed original.
''',
)
replace_once(
    "AGENTS.md",
    '''Keep transactions bounded and preserve exact bytes, hashes, mappings, topology and provenance.
Owner-only include-topology changes must update their source-index and affected header-owner
authority in the same transaction. Added or removed governed local headers require the explicit
`authority-checkout -> authority-prepare -> authority-verify -> authority-apply` migration path and
must not be smuggled through an ordinary patch. Translation-unit membership or normalized compiler-
context changes are refused with `BUILD_AUTHORITY_MIGRATION_REQUIRED` until a project-specific build-
authority migration can reproduce the real build configuration. The generic migration consumes an
isolated candidate tree and compiler database; it never guesses or rewrites the project build system. After any failed command, inspect state before retrying. Do not widen a scope or disable a gate to make a refusal disappear. Git and
remote publication require an explicit, separate approval.
''',
    '''Keep transactions bounded and preserve exact bytes, hashes, mappings, topology and provenance.
Existing governed files use `checkout -> prepare -> verify -> apply`. Create/delete/rename of static-
ecosystem sources use `source-set-checkout -> source-set-prepare -> source-set-verify -> source-set-apply`;
the live project remains unchanged until verified apply. Candidate build/config changes and C-family
topology are refused by that generic source-set path. C-family owner-only header changes remain derived
inside the ordinary transaction; governed-header topology uses the compiler-backed authority migration,
and compiler-context/build-authority changes require stronger project-specific proof. After any failed
command, inspect state before retrying. Do not widen a scope or disable a gate to make a refusal disappear.
Git and remote publication require an explicit, separate approval.
''',
)

# Framework bootstrap: point a cold agent directly at the universal onboarding contract.
replace_once("docs/FRAMEWORK_BOOTSTRAP.md", 'revision = 1\n', 'revision = 2\n')
replace_once("docs/FRAMEWORK_BOOTSTRAP.md", 'updated_at = "2026-09-08T10:30:00Z"', f'updated_at = "{STAMP}"')
insert_before(
    "docs/FRAMEWORK_BOOTSTRAP.md",
    '[refs]\n',
    '''[[answers]]
intent = "onboarding"
question = "How do I onboard an arbitrary existing codebase into KAIROS?"
target = "s-handoff-into-a-project"

''',
)
replace_once(
    "docs/FRAMEWORK_BOOTSTRAP.md",
    'kickstart = "[ref:docs/PROJECT_KICKSTART.md#s-1-project-intent-precedes-code-discovery|id:KAIROS_PROJECT_KICKSTART|v:1|rel:next|tags:kickstart,intent|src:framework]"',
    'kickstart = "[ref:docs/PROJECT_KICKSTART.md#s-universal-auto-intake|id:KAIROS_PROJECT_KICKSTART|v:2|rel:next|tags:kickstart,auto|src:framework]"\nonboarding = "[ref:docs/UNIVERSAL_ONBOARDING.md#s-cold-onboarding|id:KAIROS_UNIVERSAL_ONBOARDING|v:1|rel:next|tags:onboarding,universal|src:framework]"',
)
replace_once(
    "docs/FRAMEWORK_BOOTSTRAP.md",
    '''Use question-shaped queries rather than asking the model to reconstruct rules from directory layout. Useful first questions include how project intent is established, what the compiler owns, how source inspection is permitted, how the Workshop changes governed code, what requires authority migration, and what a successful postcheck does not prove.
''',
    '''Use question-shaped queries rather than asking the model to reconstruct rules from directory layout. For a new codebase, ask `How do I onboard an arbitrary existing codebase into KAIROS?`, then ask `After KAIROS seals a project, what is allowed to modify source code or Markdown?`. The returned universal onboarding contract supplies `detect`, `--auto`, the C-family compiler boundary, clone-before-risk, ordinary Workshop mutation and source-set transactions.
''',
)
replace_once(
    "docs/FRAMEWORK_BOOTSTRAP.md",
    '''After the Human goal is converted into a reviewed `kairos-project-kickoff/v1` contract, project intake may create a fresh project workspace and project database from compiler evidence. From that point, project questions use `kairos search --workspace <path> ...`; framework-rule questions remain available through the workspace-free search path.
''',
    '''After framework search retrieves the universal onboarding contract, the agent runs `kickstart detect <project>`, establishes the reviewed Human `kairos-project-kickoff/v1` contract, and runs `kickstart init --auto`. Static ecosystems use bounded exact source membership; C/C++/CUDA remains compiler-backed. Intake observes the original project read-only, creates the initial KAIROS/Workshop baseline, and returns `VERIFIED_PENDING_SEAL`. The agent then seals through Workshop. From that point, project questions use `kairos search --workspace <path> ...`; framework-rule questions remain available through the workspace-free search path, and all governed mutations go through Workshop transactions.
''',
)

# Project kickstart: add the universal path while preserving the stronger C-family subsection.
replace_once("docs/PROJECT_KICKSTART.md", 'revision = 1\n', 'revision = 2\n')
replace_once("docs/PROJECT_KICKSTART.md", 'updated_at = "2026-09-08T10:30:00Z"', f'updated_at = "{STAMP}"')
replace_once(
    "docs/PROJECT_KICKSTART.md",
    'capsule = "Authoritative project-initiation procedure: Human intent first, compiler-backed membership second, then exact Markdown/database baseline and Workshop handoff."',
    'capsule = "Authoritative project-initiation procedure: framework search and Human intent first, universal detect/auto intake second, compiler-backed C-family authority where required, then exact Markdown/database baseline and Workshop handoff."',
)
replace_once(
    "docs/PROJECT_KICKSTART.md",
    'facets = ["kickstart", "intent", "compiler", "ground-truth"]',
    'facets = ["kickstart", "intent", "universal-intake", "compiler", "ground-truth"]',
)
insert_before(
    "docs/PROJECT_KICKSTART.md",
    '[[search_contract]]\n',
    '''[[answers]]
intent = "universal_intake"
question = "How do I initialize a Python Rust JavaScript TypeScript Go Java .NET Ruby PHP or mixed project with KAIROS?"
target = "s-universal-auto-intake"

''',
)
replace_once(
    "docs/PROJECT_KICKSTART.md",
    '''The canonical framework is intentionally unbound. A real project is created in two ordered
steps: first establish Human project intent, then bind compiler-observed implementation
reality under that exact goal/milestone/task scope.
''',
    '''The canonical framework is intentionally unbound. A real project begins by retrieving the
framework onboarding contract, establishing Human intent, detecting the ecosystem(s), and binding
observed implementation reality under that exact goal/milestone/task scope. Static ecosystems use
bounded exact source membership; C-family translation units retain stronger compiler authority.
''',
)
insert_before(
    "docs/PROJECT_KICKSTART.md",
    '<a id="s-2-compiler-backed-implementation-binding"></a>\n',
    '''<a id="s-universal-auto-intake"></a>
## 2. Universal detect and auto intake

> Capsule: `detect` identifies supported ecosystems without mutating the project; `init --auto` creates the exact initial KAIROS/Workshop baseline while preserving compiler-backed C-family membership.

From the release root:

```text
python -m kickstart detect <project>
python -m kickstart init --auto --project-root <project> --workspace <fresh-kairos-workspace> --spec-file project-spec.json
```

Supported static ecosystems are Python, JavaScript/TypeScript, Rust, Go, Java/Kotlin/Groovy,
.NET, Ruby and PHP. Their initial authority is exact project-local source membership with byte
hashes and verbatim ledgers; dynamic imports, runtime reachability and build participation are not
invented. Generated/vendor trees are excluded.

If C/C++/CUDA translation units are detected, `--auto` additionally requires compiler-produced
membership:

```text
python -m kickstart init --auto --project-root <project> --workspace <fresh-kairos-workspace> --spec-file project-spec.json --auto-compile-commands <build>/compile_commands.json
```

Mixed projects combine static and compiler-backed authority without treating Python/Rust/etc. as
C-family translation units. Initial intake reads the governed project and writes the KAIROS workspace;
it does not modify the governed project. If an analysis/build tool could write project or build files,
it must run against an isolated clone/snapshot.

''',
)
replace_once(
    "docs/PROJECT_KICKSTART.md",
    '## 2. Compiler-backed implementation binding\n',
    '## 3. C-family compiler-backed implementation binding\n',
)
replace_once(
    "docs/PROJECT_KICKSTART.md",
    '''A verified intake may enter the separate Workshop lifecycle:

```text
status -> seal -> checkout -> edit transaction work tree -> metadata review
       -> prepare -> verify -> apply -> postcheck/reseal
```
''',
    '''A verified universal intake returns `VERIFIED_PENDING_SEAL` and then enters Workshop:

```text
status -> seal
existing file: checkout -> edit transaction work tree -> metadata review -> prepare -> verify -> apply -> POSTCHECK_VERIFIED
source-set: source-set-checkout -> edit candidate tree -> source-set-prepare -> source-set-verify -> source-set-apply -> POSTCHECK_VERIFIED
```

After seal, Workshop is the only mutation path for governed source and governed Markdown. Direct
live edits followed by rediscovery or documentation repair are forbidden.
''',
)

# Workshop: document static source-set mutation and make ordinary edits language-agnostic.
replace_once("docs/WORKSHOP.md", 'revision = 1\n', 'revision = 2\n')
replace_once("docs/WORKSHOP.md", 'updated_at = "2026-09-08T10:30:00Z"', f'updated_at = "{STAMP}"')
replace_once(
    "docs/WORKSHOP.md",
    'capsule = "Authoritative synchronized-change lifecycle for governed code/headers, metadata, shadow verification, postcheck and authority migration."',
    'capsule = "Authoritative synchronized-change lifecycle for universal governed source, C-family headers, source-set changes, metadata, shadow verification, postcheck and authority migration."',
)
replace_once(
    "docs/WORKSHOP.md",
    'facets = ["workshop", "transaction", "topology", "authority-migration"]',
    'facets = ["workshop", "transaction", "source-set", "topology", "authority-migration"]',
)
insert_before(
    "docs/WORKSHOP.md",
    '[[search_contract]]\n',
    '''[[answers]]
intent = "source_set"
question = "How do I create delete or rename governed source files after seal?"
target = "s-universal-source-set-mutation"

''',
)
replace_once(
    "docs/WORKSHOP.md",
    '''The Workshop is a fail-closed synchronizer for a separately configured codebase, its
implementation documents and its KAIROS projection. It is shipped unbound and becomes
project-specific only through compiler-backed intake.
''',
    '''The Workshop is a fail-closed synchronizer for a separately configured codebase, its
implementation documents and its KAIROS projection. It is shipped unbound and becomes
project-specific through universal intake. Static ecosystems carry bounded source membership;
C/C++/CUDA retains compiler-backed translation-unit/header authority.
''',
)
replace_once(
    "docs/WORKSHOP.md",
    '''Work is edited only under the transaction `work/` tree. `prepare` regenerates mechanical
hash/ledger layers and requires semantic document review whenever the C/C++ token stream
changes. Mutable file facts have one mechanical owner: generated SOURCE MAPPING/ledger
layers. Project intake does not duplicate those changing hashes in an independently stale
semantic section.
''',
    '''Work is edited only under the transaction `work/` tree. `prepare` regenerates mechanical
hash/ledger layers. C-family retains its token-stream review rule; static universal ecosystems do
not acquire fake C++ token semantics and advance their exact mechanical mirrors on byte changes.
Mutable file facts have one mechanical owner: generated SOURCE MAPPING/ledger layers. Project
intake does not duplicate those changing hashes in an independently stale semantic section.
''',
)
insert_before(
    "docs/WORKSHOP.md",
    '<a id="s-authority-migration"></a>\n',
    '''<a id="s-universal-source-set-mutation"></a>
## Universal static source-set mutation

> Capsule: Create, delete and rename of static-ecosystem source files are Workshop-owned candidate transactions; the live project is unchanged until shadow-verified apply.

Python/JavaScript/TypeScript/Rust/Go/JVM/.NET/Ruby/PHP source-set changes use:

```text
source-set-checkout -> edit isolated candidate -> source-set-prepare -> source-set-verify -> source-set-apply
```

`source-set-checkout` copies the sealed governed project into Workshop transaction staging. The
agent creates/deletes/renames/edits only inside that candidate. `source-set-prepare` recomputes the
bounded static source membership and generated Markdown/machine authority. `source-set-verify`
builds and verifies the candidate KAIROS/Workshop corpus in isolation. `source-set-apply` is the only
step allowed to alter live source membership; it advances source files, active/tombstoned Markdown,
machine authority, database projection and seal together, ending at `POSTCHECK_VERIFIED`.

The generic source-set transaction fails closed if build/config authority changes or C-family source/
header topology changes. Those transitions use stronger authority paths; the static source-set path
never rewrites build configuration or weakens compiler-backed C-family evidence.

''',
)

# Extend deterministic framework gold queries with the exact step-2 contract.
insert_before(
    "scripts/build_framework_index.py",
    '''    (
        "How should an LLM acquire context in KAIROS?",
''',
    '''    (
        "How do I onboard an arbitrary existing codebase into KAIROS?",
        ("KAIROS_UNIVERSAL_ONBOARDING#s-cold-onboarding",),
        1,
    ),
    (
        "After KAIROS seals a project, what is allowed to modify source code or Markdown?",
        ("KAIROS_UNIVERSAL_ONBOARDING#s-single-mutation-boundary",),
        1,
    ),
    (
        "How do I create delete or rename governed source files after seal?",
        ("KAIROS_UNIVERSAL_ONBOARDING#s-source-set-transactions",),
        1,
    ),
''',
)

# Extend framework bootstrap tests so the rebuilt immutable DB must answer the new rules directly.
replace_once(
    "kairos/kairos_harness/tests/test_framework_bootstrap.py",
    '''            'How should an LLM acquire context in KAIROS?': ('KAIROS_LLM_OPERATING_CONTRACT', 's-context-policy'),
''',
    '''            'How should an LLM acquire context in KAIROS?': ('KAIROS_LLM_OPERATING_CONTRACT', 's-context-policy'),
            'How do I onboard an arbitrary existing codebase into KAIROS?': ('KAIROS_UNIVERSAL_ONBOARDING', 's-cold-onboarding'),
            'After KAIROS seals a project, what is allowed to modify source code or Markdown?': ('KAIROS_UNIVERSAL_ONBOARDING', 's-single-mutation-boundary'),
            'How do I create delete or rename governed source files after seal?': ('KAIROS_UNIVERSAL_ONBOARDING', 's-source-set-transactions'),
''',
)

print("v3 step2 framework contract patch applied")
