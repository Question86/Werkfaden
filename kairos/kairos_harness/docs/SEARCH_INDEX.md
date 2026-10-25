+++
schema = "kairos-context/v1"
id = "KAIROS_SEARCH_INDEX"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_SEARCH_INDEX"
updated_at = "2026-09-08T10:30:00Z"
capsule = "Index of KAIROS retrieval surfaces and the question shapes appropriate for section search, graph search and bounded source inspection."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_SEARCH_INDEX"]
facets = ["search", "graph", "source-search", "routing"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "search"
question = "How do I ask KAIROS for context?"
target = "s-choosing-a-door"

[[answers]]
intent = "search"
question = "How does kairos search work?"
target = "s-kairos-search-query"

[[answers]]
intent = "source_search"
question = "How do I reach actual source code through KAIROS?"
target = "s-reaching-actual-source"

[[search_contract]]
query = "How do I ask KAIROS for context?"
expected = "KAIROS_SEARCH_INDEX#s-choosing-a-door"
required_top_k = 1
+++
# Retrieval index — every way to ask KAIROS something

## CONTEXT INDEX

- [`s-overview`](#s-overview) — One table per surface: the command, what it returns, and the question it is the right
- [`s-choosing-a-door`](#s-choosing-a-door) — Index of KAIROS retrieval surfaces and the question shapes appropriate for section search, graph search and bounded source inspection.
- [`s-kairos-search-query`](#s-kairos-search-query) — Full-text over promoted sections, plus a graph route when the query names identities the
- [`s-kairos-graph`](#s-kairos-graph) — Exact lookups against the four graph tables. All identity matching is exact — see
- [`s-reaching-actual-source`](#s-reaching-actual-source) — Three steps, and the order is enforced.
- [`s-picking-the-right-surface-for-a-counting-question`](#s-picking-the-right-surface-for-a-counting-question) — Search cannot count; it returns sections. These are the four that can:
- [`s-related`](#s-related) — OPERATIONS.md — the surrounding session flow: entry, freshness, heartbeat, closure

<a id="s-overview"></a>
## Overview

> Capsule: One table per surface: the command, what it returns, and the question it is the right

One table per surface: the command, what it returns, and the question it is the right
answer to. Written for an operator who knows what they want to know but not yet which
door to use.

Everything here is read-only. Every call spends a one-use permit and writes a receipt,
so a wrong door costs a ledger entry, not just time.

`RETRIEVAL_METHOD.md` is the companion: which door to pick when several could work, with
the measurements behind each rule. This page is the reference; that one is the decision.

<a id="s-choosing-a-door"></a>
## Choosing a door

> Capsule: Index of KAIROS retrieval surfaces and the question shapes appropriate for section search, graph search and bounded source inspection.

```
I have a question in words                     -> search
I have a name and want its edges               -> graph --node
I have a partial name, or a lookup came back empty
                                               -> graph --resolve
I have a file the run produced                 -> graph --asset
I have one document and want all of it         -> graph --artifact
I want a whole table across the corpus         -> graph --inventory
I have a starting point and want the neighbourhood
                                               -> graph --chase
I need the bytes of a source file              -> search -> source-permit -> source-search
```

<a id="s-kairos-search-query"></a>
## kairos search <query>

> Capsule: Full-text over promoted sections, plus a graph route when the query names identities the

Before project intake, omit `--workspace`: the same command queries the immutable, hash-verified
framework corpus bundled with the harness. This mode creates no project state and records no
retrieval trace. After project intake, supply `--workspace <path>` to query the mutable project
projection under its normal freshness and governance boundary.

Full-text over promoted sections, plus a graph route when the query names identities the
graph stores.

| Flag | Effect |
|---|---|
| `--mode {depth,breadth,breathe,work,verify}` | how wide the retrieval spreads |
| `--limit N` | result sections returned |
| `--candidate-limit N` | rows scored before ranking; raise it when the answer is crowded out, not when it is missing |
| `--max-hops N` | hops for the graph chase attached to the result |
| `--no-refresh` | query a possibly stale projection |
| `--trace` / `--no-trace` | persist the retrieval trace |

**Good for:** a question you can phrase in prose. A symptom description. Finding out
*which* document owns a topic when you cannot name it yet.

**Not good for:** counting. Search returns sections, not aggregates — see `--inventory`.

**The one thing worth knowing.** Naming an exact document identifier in the query pins
the whole result set to that one document, because the exact-identifier bonus dominates
every other signal. That is right when you want to open a known document and wrong when
you want to find one. Prose finds; identifiers open.

Rare names beat common ones. An identifier that occurs in one document routes the graph
usefully; one that occurs in nine (`artifact_root`, a widely shared asset id) drags in
nine anchors that all score alike and can bury the document you wanted.

The result carries a `routing_receipt`. That receipt is the only legitimate way into
`source-permit`, and it is single-use.

<a id="s-kairos-graph"></a>
## kairos graph

> Capsule: Exact lookups against the four graph tables. All identity matching is exact — see

Exact lookups against the four graph tables. All identity matching is exact — see
*Misses* below.

| Flag | Returns | Good for |
|---|---|---|
| `--artifact ID` | every declared row of one document, table by table, with true totals | reading one blueprint's whole structure: its symbols, contracts, assets, drift |
| `--node NAME` | relations naming the entity in either position, plus its assets and contracts | "what touches this function/file/type, and what does it touch" |
| `--resolve PARTIAL` | canonical identities ending in the partial, each with a hit count and the column it lives in | turning a bare symbol off a stack trace into the identity the graph stores. **Never substitutes** — it lists, you choose |
| `--asset PATH` | who declares this asset, with role and operation, plus edges pointing at it | starting from a file the run produced and walking back into the corpus |
| `--predicate NAME` | every relation carrying that predicate | "show me every `gates`" — the corpus-wide view of one edge type |
| `--chase A,B,C` | breadth-first walk outward from one or more entities; `--predicate` narrows which edges are followed, `--max-hops` bounds the depth | the neighbourhood of a symptom, when you do not know in advance how far the cause sits |
| `--inventory {relations,contracts,artifacts,drift}` | one whole table, grouped and bounded; `--field`/`--value` narrow it | counting and distributions. The answer to "how many", "which are unresolved", "what is the spread" |
| `--vocabulary` | stored values outside their declared set | finding typos and invented values before they spread |
| `--integrity` | rows whose evidence anchor resolves to no section | finding claims that cite nothing |
| `--census` | row counts per table | orientation: how big is this corpus |

`--limit N` bounds the seven lookup surfaces above, and each of them reports the true
total next to what it returned, so a truncated answer says it is truncated. The three
report surfaces — `--census`, `--vocabulary`, `--integrity` — are whole-corpus by
definition and ignore `--limit`: a bounded integrity report would be worthless, since
the rows it did not check are exactly the ones you needed it to check.

### Misses

Identity matching is exact, and the graph stores qualified names. `--node BuildProbeV1`
finds nothing even when `app::intake::BuildProbeV1` is in there. Since an
empty result would otherwise be indistinguishable from "the corpus does not know this",
a miss on `--node`, `--asset` and `--predicate` carries a `no_match` block that says
which of the two it is:

```
no_match.reason  no row names this identity exactly
no_match.hint    the graph stores this name in a qualified form; retry with one of
                 the candidates below, or use --resolve to list them
no_match.candidates  [ {identity, hits, where} ]
```

When nothing ends in the name either, it says so plainly — *the corpus does not mention
this name at all* — and that is a real answer, not a failure.

For `--predicate` the vocabulary is closed, so a miss lists the declared predicates
instead of guessing.

<a id="s-reaching-actual-source"></a>
## Reaching actual source

> Capsule: Three steps, and the order is enforced.

Three steps, and the order is enforced.

| Step | Command | Yields |
|---|---|---|
| 1 | `search …` | `routing_receipt.routing_receipt_id` → `SRR_…` |
| 2 | `source-permit --routing-receipt SRR_… --purpose … --path … --pattern … --reason …` | `SIP_…`, with a TTL and a frozen file snapshot (size + mtime) |
| 3 | `source-search --permit SIP_…` | matching lines, plus an inspection receipt `SIR_…` |

`--purpose` is one of `implementation_verification`, `exact_location`, `contradiction`,
`negative_proof`, `exact_file_request`, `metadata_repair`. `--emergency` exists only for
`metadata_repair` when SQLite itself is unhealthy.

**Three things that will bite you.**

*One receipt, one permit.* A second `source-permit` on the same `SRR_` is refused with
*routing receipt has already been exchanged*. Each escalation needs its own search.

*Paths are relative to `inspection_root`, not to the workspace.* With
`inspection_root: ".."` a workspace document is `kairos_workspace/code/foo.md`, not
`code/foo.md`. The error says "root-relative" without naming the root; check
`.kairos/config.json`.

*Anything outside `inspection_root` is unreachable, full stop.* A live source tree that
sits elsewhere on disk cannot be permitted at any purpose. That is not a gap to work
around — it is the boundary of what KAIROS governs, and reading beyond it belongs to
whatever tool you use for the filesystem.

*`source-search` is a literal line grep.* In a governed document the TOML header repeats
the section vocabulary, so a semantic pattern like `root cause|resolution` matches the
header's own `facets` and `question` fields before it reaches the prose. Pattern on
words that only occur in the body.

<a id="s-picking-the-right-surface-for-a-counting-question"></a>
## Picking the right surface for a counting question

> Capsule: Search cannot count; it returns sections. These are the four that can:

Search cannot count; it returns sections. These are the four that can:

| Question shape | Surface |
|---|---|
| how many X are there, how do they distribute | `--inventory` with `--field`/`--value` |
| how big is the corpus | `--census` |
| what is declared but wrong | `--vocabulary` |
| what is claimed but unanchored | `--integrity` |

<a id="s-related"></a>
## Related

> Capsule: OPERATIONS.md — the surrounding session flow: entry, freshness, heartbeat, closure

- `OPERATIONS.md` — the surrounding session flow: entry, freshness, heartbeat, closure
- `INGESTED_DOCUMENT_SPEC.md` — what the four graph tables mean and how identity is formed
- `LLM_OPERATING_CONTRACT.md` — the obligations on the caller
