+++
schema = "kairos-context/v1"
id = "KAIROS_LLM_OPERATING_CONTRACT"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_LLM_OPERATING_CONTRACT"
updated_at = "2026-09-08T10:30:00Z"
capsule = "Binding behavioral contract for an LLM using KAIROS: retrieval-first orientation, authority discipline, source escalation and bounded context."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_LLM_OPERATING_CONTRACT"]
facets = ["llm", "operating-contract", "retrieval", "authority"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "llm_rules"
question = "What are the mandatory rules for an LLM using KAIROS?"
target = "s-mandatory-rules"

[[answers]]
intent = "authority"
question = "How should an LLM resolve authority in KAIROS?"
target = "s-authority-policy"

[[answers]]
intent = "retrieval"
question = "How should an LLM acquire context in KAIROS?"
target = "s-context-policy"

[[search_contract]]
query = "What are the mandatory rules for an LLM using KAIROS?"
expected = "KAIROS_LLM_OPERATING_CONTRACT#s-mandatory-rules"
required_top_k = 1
+++
# LLM Operating Contract

## CONTEXT INDEX

- [`s-overview`](#s-overview) — This contract is materialized as AGENTS.md in each KAIROS workspace.
- [`s-mandatory-rules`](#s-mandatory-rules) — 1. Write source, metadata, queries, receipts, tests, and generated artifacts in English.
- [`s-authority-policy`](#s-authority-policy) — Authority depends on the question. AGENTS.md owns procedure; current.json owns live identifiers; the loop gate owns allowed transitions; goal and task contracts own required work; promoted evidence owns factual outcome claims within its bou
- [`s-context-policy`](#s-context-policy) — Use depth for one causal, dependency, provenance, implementation, or evidence path.
- [`s-scaling-and-safety`](#s-scaling-and-safety) — Respect the enforced managed-document, goal, header, section, reference, query, candidate, result, hop, router, generated-file, and trace bounds. Follow omitted-count database routes instead of expanding dynamic pointer files. Never resolve

<a id="s-overview"></a>
## Overview

> Capsule: This contract is materialized as AGENTS.md in each KAIROS workspace.

This contract is materialized as `AGENTS.md` in each KAIROS workspace. Before any project
workspace exists, the packaged framework corpus is already searchable: use `kairos search
"<question>"` without `--workspace` to retrieve the governing KAIROS rule rather than inferring
procedure from filenames or directory layout.

<a id="s-mandatory-rules"></a>
## Mandatory rules

> Capsule: 1. Write source, metadata, queries, receipts, tests, and generated artifacts in English.

1. Write source, metadata, queries, receipts, tests, and generated artifacts in English.
2. Read operating procedure, live state, transition gate, active task, and evidence under their question-scoped authorities.
3. Search by an explicit question before broad inspection. Before project intake, use the
   workspace-free framework search for KAIROS procedure; after intake, use the project workspace
   for project facts and state.
4. Treat governed Markdown and goal contracts as source; treat SQLite, manifests, runtime files, and routers as derived state.
5. Choose one bounded work, depth, breadth, breathe, or verify action.
6. Document material work in the appropriate artifact type and promote it in the same heartbeat.
7. Preserve source facts separately from interpretation, and preserve contradictions and negative claim scope.
8. Do not edit generated routers or SQLite directly.
9. Use normal access with freshness enabled; an explicit stale diagnostic cannot prove current state.
10. Never claim completion while success evidence, a required artifact type, promotion, source, archive, backup, or verification gate is open.

<a id="s-authority-policy"></a>
## Authority policy

> Capsule: Authority depends on the question. AGENTS.md owns procedure; current.json owns live identifiers; the loop gate owns allowed transitions; goal and task contracts own required work; promoted evidence owns factual outcome claims within its bou

Authority depends on the question. `AGENTS.md` owns procedure; `current.json` owns live identifiers; the loop gate owns allowed transitions; goal and task contracts own required work; promoted evidence owns factual outcome claims within its boundary; the architecture atlas owns placement; metadata only locates these sources.

No timestamp, filename, score, or router can override the owning source.

<a id="s-context-policy"></a>
## Context policy

> Capsule: Use depth for one causal, dependency, provenance, implementation, or evidence path.

- Use `depth` for one causal, dependency, provenance, implementation, or evidence path.
- Use `breadth` when branch selection is uncertain or contradiction is plausible.
- Use `breathe` to checkpoint the exact restart frontier.
- Use `verify` to reopen evidence independently before a claim transition.

Search scores determine inspection order only.

### Retrieval policy

For a project session, read `RETRIEVAL_METHOD.md` before the first project retrieval call. The
pre-project framework search is a narrower bootstrap exception: it exists specifically so the
model can retrieve that operating guidance before a project database exists. Once project state
exists, the measured decision procedure binds normally, and the same question can cost up to
three orders of magnitude more when the wrong surface is used. Four rules from it bind here:

1. Classify the question before choosing a surface. `search` is not the default entry
   point; it cannot aggregate and cannot reach the file system.
2. A counting or distribution question goes to `--census`, then `--inventory`. Never build
   a corpus total by looping per-document reads.
3. Resolve a name before naming it. `--resolve` reports how many documents carry it: at
   one or two, query it directly; from three, describe the subject in prose instead; at
   twenty-six or more, keep the identifier out of the query entirely.
4. An empty exact lookup is not a finding until its `no_match` block says the corpus
   carries no qualified form of the name. "Stored under a qualified name" and "absent from
   the corpus" are different answers and only the second may be cited.

<a id="s-scaling-and-safety"></a>
## Scaling and safety

> Capsule: Respect the enforced managed-document, goal, header, section, reference, query, candidate, result, hop, router, generated-file, and trace bounds. Follow omitted-count database routes instead of expanding dynamic pointer files. Never resolve

Respect the enforced managed-document, goal, header, section, reference, query, candidate, result, hop, router, generated-file, and trace bounds. Follow omitted-count database routes instead of expanding dynamic pointer files. Never resolve a workspace pointer outside the workspace. Frozen provenance directories are not executable, indexable, or authoritative.
