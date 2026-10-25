+++
schema = "kairos-context/v1"
id = "KAIROS_INGESTED_DOCUMENT_SPEC"
type = "documentation"
revision = 1
state = "active"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_INGESTED_DOCUMENT_SPEC"
updated_at = "2026-09-08T10:30:00Z"
capsule = "Normative transfer format for ingested documents, normalized graph structures, evidence anchors and enforcement/reporting boundaries."
claim_boundary = "This framework source owns the stable architecture it states; live project state, project-specific evidence, and execution results remain owned by their project authorities."
entities = ["KAIROS", "KAIROS_INGESTED_DOCUMENT_SPEC"]
facets = ["ingestion", "graph", "evidence", "schema"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "ingestion"
question = "How does KAIROS ingest an external structured document?"
target = "s-the-two-step-transfer"

[[answers]]
intent = "graph"
question = "How are ingested code-graph relations represented in KAIROS?"
target = "s-header"

[[answers]]
intent = "evidence"
question = "What are evidence anchors in an ingested KAIROS document?"
target = "s-evidence-anchors"

[[search_contract]]
query = "How does KAIROS ingest an external structured document?"
expected = "KAIROS_INGESTED_DOCUMENT_SPEC#s-the-two-step-transfer"
required_top_k = 1
+++
# KAIROS Ingested Document Specification

## CONTEXT INDEX

- [`s-overview`](#s-overview) — This is the contract a project fulfils so that its documents, and the normalized graph they
- [`s-purpose`](#s-purpose) — A project produces one document per unit it describes. Each document is two things at once:
- [`s-the-two-step-transfer`](#s-the-two-step-transfer) — KAIROS never writes into the project's own tree, and never rewrites a document to fit
- [`s-origin`](#s-origin) — A document that was authored in another workspace declares that workspace in its header.
- [`s-header`](#s-header) — The ordinary kairos-context/v1 header applies; see CONTEXT_HEADER_SPEC.md. An ingested
- [`s-canonical-node-identity`](#s-canonical-node-identity) — The graph joins on exact strings. Choose an identifier in this order and never invent one
- [`s-evidence-anchors`](#s-evidence-anchors) — Every graph row names the narrowest section that substantiates it. That anchor must exist
- [`s-optional-a-source-ledger-section`](#s-optional-a-source-ledger-section) — A document may carry a line-numbered copy of the material it describes in its own section.
- [`s-what-kairos-enforces-and-what-it-only-reports`](#s-what-kairos-enforces-and-what-it-only-reports) — Enforced, fail-closed, because the document is otherwise unusable:
- [`s-reading-the-graph`](#s-reading-the-graph) — Every response carries its own total, so a truncated answer is visible as truncated rather
- [`s-bounds`](#s-bounds) — | bound | value | applies to |

<a id="s-overview"></a>
## Overview

> Capsule: This is the contract a project fulfils so that its documents, and the normalized graph they

This is the contract a project fulfils so that its documents, and the normalized graph they
carry, are transferred into KAIROS unchanged and become queryable.

It is deliberately project-neutral. Nothing here names a language, a codebase or a domain.
A project decides *what* it describes; this document decides *how* the description is shaped
so KAIROS can carry it without interpreting it.

<a id="s-purpose"></a>
## Purpose

> Capsule: A project produces one document per unit it describes. Each document is two things at once:

A project produces one document per unit it describes. Each document is two things at once:

1. **evidence** — the detailed, human-readable account of that unit;
2. **a graph node** — normalized, typed facts about how that unit connects to others.

The graph answers *what is connected to what, under which contract, producing which asset,
and where the evidence sits*. The document body answers *what the unit actually does*. An
agent queries the graph first and opens the named anchor second. That order is the point:
it is what keeps the reading cost bounded as the corpus grows.

<a id="s-the-two-step-transfer"></a>
## The two-step transfer

> Capsule: KAIROS never writes into the project's own tree, and never rewrites a document to fit

```
project produces documents  ->  documents are placed in a KAIROS document root
                            ->  promotion projects them into SQLite
```

KAIROS never writes into the project's own tree, and never rewrites a document to fit
itself. Where a document and the harness disagree on a bound, the harness is the side that
changes. Where a document contradicts its own declared vocabulary, the row is stored as
written and reported as a finding.

<a id="s-origin"></a>
## Origin

> Capsule: A document that was authored in another workspace declares that workspace in its header.

A document that was authored in another workspace declares that workspace in its header.
The host lists the origins it accepts in `.kairos/config.json`:

```json
"imported_workspace_ids": ["THE_ORIGIN_WORKSPACE_ID"],
"imported_search_contract_policies": {"THE_ORIGIN_WORKSPACE_ID": "origin_recorded"}
```

An undeclared origin is refused. The policy mapping is optional and origin-specific.
Omitting it keeps the normal `host_enforced` search-contract gate. `origin_recorded`
is only for a declared import whose ranking contract belongs to an origin corpus that the
host does not reproduce.

A declared origin is *recorded, not resolved*: the
document keeps its own `goal`, `milestone` and `task` identifiers exactly as written, and
KAIROS does not look them up in the host contracts. Its `[refs]` pointers are recorded as
external leaves — typed and stored, never followed, never expanded.

This is what makes the corpus portable. The document remains true in its own namespace.

<a id="s-header"></a>
## Header

> Capsule: The ordinary kairos-context/v1 header applies; see CONTEXT_HEADER_SPEC.md. An ingested

The ordinary `kairos-context/v1` header applies; see `CONTEXT_HEADER_SPEC.md`. An ingested
document additionally may declare any of four normalized arrays. Declaring at least one of
them makes the document *graph-bearing*.

Each array maps to one table. The column names are the field names. Nothing is renamed,
normalized or inferred on the way in.

### `[[relations]]` — typed edges

```toml
[[relations]]
subject = "<canonical node>"
predicate = "<controlled predicate>"
object = "<canonical node>"
object_kind = "module|source|header|symbol|type|artifact|schema|contract|failure|concept"
scope = "<short machine-readable qualifier>"
evidence_target = "s-<anchor>"
evidence = "direct"
```

Predicates: `defines`, `declares`, `depends_on`, `calls`, `delegates_to`, `consumes`,
`produces`, `reads`, `writes`, `commits`, `validates`, `authenticates`, `binds`, `gates`,
`implements`, `owns`, `constrained_by`, `precedes`, `supersedes`, `drifts_from`, `tracks`.

Only one direction is declared. Inverse edges are derived by the reader, never stored twice.

### `[[contracts]]` — invariants and authority rules

```toml
[[contracts]]
id = "<stable id, local to this document>"
kind = "invariant|precondition|postcondition|authority_boundary|identity_binding|hash_binding|ordering|fail_closed|compatibility|admission"
subject = "<canonical node>"
statement = "<precise compact statement>"
failure_or_effect = "<exact failure literal or effect, or empty>"
evidence_target = "s-<anchor>"
```

Promote a rule here only when it matters across unit boundaries. A local precondition
belongs in the body.

### `[[artifacts]]` — persistent or semantically central assets

```toml
[[artifacts]]
id = "<exact asset identity or path>"
role = "input|output|snapshot|sidecar|commit_marker|manifest|report|registry|config"
operation = "read|write|commit|embed|hash|verify"
producer_or_consumer = "<canonical node>"
schema_or_type = "<exact schema or type, or empty>"
hash_bound = true
commit_bound = false
evidence_target = "s-<anchor>"
```

`id` is the asset, not a KAIROS document id. This is the column an observed artifact is
looked up by, so it must be the identity that actually appears in the world: the path or
name a reader would see. `hash_bound` and `commit_bound` are set only where the described
unit genuinely establishes that property.

### `[[drift_records]]` — measured difference against a predecessor

```toml
[[drift_records]]
id = "D1"
historical = "<predecessor reference or id>"
subject = "<canonical node>"
classification = "retained|superseded|missing_surface|implementation_drift|route_drift|provenance_boundary_deviation|other_measured"
summary = "<compact measured difference>"
status = "aligned|intentional_supersession|unresolved"
evidence_target = "s-<anchor>"
```

A record belongs here only when the difference was *measured*. A suspicion is not drift.

<a id="s-canonical-node-identity"></a>
## Canonical node identity

> Capsule: The graph joins on exact strings. Choose an identifier in this order and never invent one

The graph joins on exact strings. Choose an identifier in this order and never invent one
when a real identity exists:

1. an existing KAIROS document or entity id
2. a repository-relative source or header path
3. an exact fully qualified public symbol
4. an exact asset-relative path
5. an exact schema or version literal
6. an exact named contract, report or type
7. an exact code literal, such as a failure identifier

The same thing carries the same identifier everywhere in the corpus. The graph must join
without fuzzy matching; that property is worth more than convenient prose.

<a id="s-evidence-anchors"></a>
## Evidence anchors

> Capsule: Every graph row names the narrowest section that substantiates it. That anchor must exist

Every graph row names the narrowest section that substantiates it. That anchor must exist
in the declaring document. This is the link that turns a graph fact into evidence:

```
graph row -> document -> anchor -> the passage that proves it
```

An anchor that resolves to no section is reported by `graph --integrity`. The row survives;
the defect is named.

<a id="s-optional-a-source-ledger-section"></a>
## Optional: a source ledger section

> Capsule: A document may carry a line-numbered copy of the material it describes in its own section.

A document may carry a line-numbered copy of the material it describes in its own section.
Where a project does this, the section becomes the corpus's exact-literal surface: an
identifier observed in the wild is found through the corpus without reading the original
tree.

This is optional and it is not free. Such a section is the largest object KAIROS stores;
`MAX_SECTION_BYTES` is sized for it.

<a id="s-what-kairos-enforces-and-what-it-only-reports"></a>
## What KAIROS enforces, and what it only reports

> Capsule: Enforced, fail-closed, because the document is otherwise unusable:

Enforced, fail-closed, because the document is otherwise unusable:

- the header parses and carries the required keys
- every answer handle targets a section that exists
- every search contract expected target names a parsed section in the same document
- every host-enforced search contract retrieves that section
- an origin workspace is declared and accepted
- a declared row count equals the stored row count

Reported, never rejected, because one bad row must not cost a document that may not be
edited:

- a contract from an explicitly configured `origin_recorded` workspace; its query,
  expected target and `required_top_k` remain in the receipt with
  `status=recorded_not_resolved` and no claimed host result
- a value outside its declared vocabulary — `graph --vocabulary`
- an evidence anchor that resolves to no section — `graph --integrity`

An imported search contract is an origin-corpus ranking claim. Combining that corpus with
the host changes the candidate set, and duplicate origin queries may name more distinct
targets than `required_top_k` can contain. The explicit policy preserves imported bytes
without turning an impossible host ranking into fabricated evidence. Local contracts and
imports without that opt-in remain promotion gates.

The distinction is deliberate. A corpus is allowed to be imperfect; it is not allowed to be
silently imperfect.

<a id="s-reading-the-graph"></a>
## Reading the graph

> Capsule: Every response carries its own total, so a truncated answer is visible as truncated rather

```
kairos graph --artifact <ID>      every declared structure of one document
kairos graph --node <IDENTITY>    edges naming one entity, in either direction
kairos graph --asset <IDENTITY>   who produces or consumes an observed asset
kairos graph --predicate <NAME>   edges carrying one predicate
kairos graph --resolve <PARTIAL>  canonical identities ending in a partial name
kairos graph --chase <IDENTITY>   walk the declared edges outward, hop by hop
kairos graph --inventory <TABLE>  one whole table across the corpus, grouped
kairos graph --vocabulary         stored values outside their declared set
kairos graph --integrity          rows whose evidence anchor does not resolve
kairos graph --census             deterministic census of the graph layer
```

Every response carries its own total, so a truncated answer is visible as truncated rather
than looking complete.

### Identity is exact, and a miss says so

A row stores an entity the way its source declares it: a symbol fully qualified, a file as
a repository-relative path. Matching is exact. `BuildSourceProbeV1` therefore does not find
`app::intake::BuildSourceProbeV1`, and a caller working from a log line or a
stack trace has only the bare form.

Loosening the match would be the wrong repair. A reader who cannot tell an exact hit from a
near one cannot trust either. `--resolve` reports the canonical identities that end in a
given name, each with its occurrence count and the table and column it was found in, and
leaves the choice with the caller. `search` carries the same list as
`graph_context.near_misses` whenever a query named identifiers and none of them matched.

That leaves one hazard: an empty exact lookup and a genuinely absent entity produce the
same nothing, and nothing reads like a finding. So `--node`, `--asset` and `--predicate`
attach a `no_match` block to an empty result which says which of the two occurred — a
qualified form exists and is named, or the corpus does not carry the name at all. The
second is a real answer about the corpus and may be cited as one. The first never may.

`SEARCH_INDEX.md` puts all retrieval surfaces in one table, including this one.

### Reading a whole table

The per-document and per-entity reads answer what one thing declares. An inventory
question — every asset, every unresolved drift record, every contract of one kind — is a
different shape, and assembling it from 148 document reads costs far more than the answer.
`--inventory` returns one table across the corpus, grouped by its characteristic column,
with the true total and the document count beside the bounded rows. `--field` and `--value`
narrow it; an unknown column is refused by name rather than silently ignored.

### Walking outward

`--chase` follows `graph_relations` from named seeds, up to four hops, optionally narrowed
by `--predicate`. It answers the question a failure literal raises: what gates this, and
what does that depend on.

An edge belongs to exactly one hop. Without that rule an edge reappears on the next hop
through whichever endpoint the frontier moved to, and the chain reads as deeper than it is.
Each hop reports how many edges existed, how many were read, and how many were already
seen, so a truncated walk is visible as truncated.

`search` performs the same walk for the identifiers a query resolved, and returns it as
`graph_chase`. It is a different chain from `context_chase`, which follows declared header
references between documents; both are reported, neither replaces the other.

<a id="s-bounds"></a>
## Bounds

> Capsule: | bound | value | applies to |

| bound | value | applies to |
|---|---|---|
| header bytes | 3,072 | documents KAIROS itself generates |
| first indexed section | byte 4,096 | documents KAIROS itself generates |
| header bytes | 256 KiB | ingested documents |
| entities | 128 | graph-bearing documents |
| section bytes | 1 MiB | any section, sized for a source ledger |
| document bytes | 2 MiB | any document |
| sections per document | 128 | any document |

A document KAIROS writes keeps the tight first-window budget, because there the cheap first
read is the entire value. An ingested document is reached through the database and opened at
a named anchor, so that budget buys nothing and is not imposed on it.
