+++
schema = "kairos-context/v1"
id = "KAIROS_RETRIEVAL_METHOD"
type = "documentation"
revision = 1
state = "active"
authority = "operating_contract"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_RETRIEVAL_METHOD"
updated_at = "2026-09-08T10:30:00Z"
capsule = "Decision procedure for choosing metadata search, graph, exact source search, hop depth, source escalation and confidence boundaries."
claim_boundary = "This framework source owns the procedure and rules it states; it does not prove live project state, project outcomes, or facts outside its declared scope."
entities = ["KAIROS", "KAIROS_RETRIEVAL_METHOD"]
facets = ["retrieval", "search", "graph", "source-escalation"]
criteria = []
does_not_answer = ["live project state", "project-specific execution outcome"]

[[answers]]
intent = "retrieval"
question = "When should I use depth, breadth, breathe, work or verify search modes?"
target = "s-1-decision-table"

[[answers]]
intent = "source_search"
question = "When should KAIROS escalate from metadata search to source inspection?"
target = "s-6-the-source-escalation-is-a-fixed-three-step-chain"

[[answers]]
intent = "search"
question = "What should not be sent to KAIROS section search?"
target = "s-2-never-send-a-counting-question-to-search"

[[search_contract]]
query = "When should I use depth, breadth, breathe, work or verify search modes?"
expected = "KAIROS_RETRIEVAL_METHOD#s-1-decision-table"
required_top_k = 1
+++
# Retrieval method — how to find context without wasting it

## CONTEXT INDEX

- [`s-overview`](#s-overview) — **Read this before your first retrieval call in a session.** It is a decision procedure,
- [`s-1-decision-table`](#s-1-decision-table) — Classify the question, then run the row. Do not start with search by default.
- [`s-2-never-send-a-counting-question-to-search`](#s-2-never-send-a-counting-question-to-search) — search returns ranked sections. It has no aggregate. It cannot answer "how many".
- [`s-3-name-an-identifier-only-when-it-is-rare`](#s-3-name-an-identifier-only-when-it-is-rare) — This is the least obvious rule and the one that costs the most when violated.
- [`s-4-an-exact-string-in-files-is-rg-s-question`](#s-4-an-exact-string-in-files-is-rg-s-question) — Locating the single document that defines a rare qualified symbol:
- [`s-5-one-hop-is-cheap-anywhere-two-hops-only-in-the-graph`](#s-5-one-hop-is-cheap-anywhere-two-hops-only-in-the-graph) — Neighbourhood of one header file:
- [`s-6-the-source-escalation-is-a-fixed-three-step-chain`](#s-6-the-source-escalation-is-a-fixed-three-step-chain) — Three things that will cost you a retry:
- [`s-7-inspection-root-is-a-wall-not-a-preference`](#s-7-inspection-root-is-a-wall-not-a-preference) — KAIROS reads only what is promoted, and source-permit reaches only inside
- [`s-8-reading-an-empty-result`](#s-8-reading-an-empty-result) — An empty result is **not** automatically a finding. --node, --asset and --predicate
- [`s-9-anti-patterns`](#s-9-anti-patterns) — | Do not | Because | Instead |
- [`s-10-confidence-of-each-claim`](#s-10-confidence-of-each-claim) — | Claim | Basis |
- [`s-related`](#s-related) — SEARCH_INDEX.md — what every surface returns, flag by flag

<a id="s-overview"></a>
## Overview

> Capsule: **Read this before your first retrieval call in a session.** It is a decision procedure,

**Read this before your first retrieval call in a session.** It is a decision procedure,
not background. Following it changes token cost by up to three orders of magnitude on the
same question.

Every number here is measured on a 152-document corpus (6,197 relations, 1,118 contracts,
670 artifacts, 533 drift records) by charging the bytes each surface returns. Evidence is
at the end; the rules come first because you need them first.

---

<a id="s-1-decision-table"></a>
## 1. Decision table

> Capsule: Classify the question, then run the row. Do not start with search by default.

Classify the question, then run the row. Do not start with `search` by default.

| Your question | Surface | Cost | Rule |
|---|---|---:|---|
| What is this name, canonically? How common is it? | `graph --resolve "<partial>"` | **72** | §3 |
| Who produces/consumes one asset | `graph --asset "<path>"` | **178** | — |
| How many / what distribution, over the corpus | `graph --census` | **387** | §2 |
| Which files contain this exact string | `rg -l "<string>" <dir>` | 139 | §4 |
| Everything one named entity touches | `graph --node "<exact identity>"` | 1.1k | §4 |
| Everything one document declares | `graph --artifact <DOC_ID>` | 5.1k | — |
| …counts are not enough, I need the rows | `graph --inventory <table> --field F --value V` | 38k | §2 |
| Neighbourhood, 2+ relations out | `graph --chase "<entity>" --max-hops 2` | 21k | §5 |
| I can only describe it in words | `search "<prose>"` | ~4.2k | §3 |
| I need the actual bytes of a file | `search` → `source-permit` → `source-search` | varies | §6 |
| The file is outside `inspection_root` | `rg` / filesystem tools only | — | §7 |

---

<a id="s-2-never-send-a-counting-question-to-search"></a>
## 2. Never send a counting question to search

> Capsule: search returns ranked sections. It has no aggregate. It cannot answer "how many".

`search` returns ranked sections. It has no aggregate. It cannot answer "how many".

Same answer — the distribution of predicates across the corpus — by surface:

| Surface | Tokens | vs cheapest |
|---|---:|---:|
| `graph --census` | **387** | 1× |
| `graph --inventory relations` | 38,590 | 100× |
| loop of `--artifact` over 152 documents | 838,502 | 2,166× |

**Do:** try `--census` first for any distribution or corpus-size question. It answers
most of them outright.
**Then:** drop to `--inventory` only when you need the rows themselves, and narrow with
`--field`/`--value` *before* reading them.
**Never:** loop `--artifact` over documents to build a total. That is the 2,166× path and
it is the loop you will reach for if you have not read this section.

---

<a id="s-3-name-an-identifier-only-when-it-is-rare"></a>
## 3. Name an identifier only when it is rare

> Capsule: This is the least obvious rule and the one that costs the most when violated.

This is the least obvious rule and the one that costs the most when violated.

`df(X)` = how many documents mention identifier X. Measured over 132 (target document,
identifier) pairs, 22 per bucket, three query forms against the same target.

**Identifier alone as the query:**

| df(X) | 1 | 2 | 3–5 | 6–10 | 11–25 | 26+ |
|---|---:|---:|---:|---:|---:|---:|
| hit@1 | **100%** | 50% | 32% | 9% | 0% | 0% |
| hit@5 | 100% | 95% | 95% | 68% | 36% | 9% |
| tokens | 4,794 | 5,684 | 6,434 | 7,898 | 12,254 | 21,493 |

Accuracy collapses monotonically while cost rises 4.5×. **You pay more to be more wrong.**

**Prose alone** is flat at every df: 77–95% hit@1, ~4,200 tokens. It never sees the
identifier, so the identifier's frequency cannot hurt it.

**Prose + identifier** matches prose up to df 25, then drops to 55% hit@1 at df ≥ 26 —
41 points worse than the same prose without the identifier.

### What to do

```
graph --resolve "<the name you have>"      # 72 tokens, gives canonical form + hit count
```

**Always do this first.** At a median of 72 tokens it is the cheapest call in the system,
and it decides a branch whose wrong side costs up to 21,493 tokens at 0% hit@1. There is
no budget argument against running it.

Then branch on the hit count:

| df | Action |
|---|---|
| **1–2** | Query the identifier directly, or go straight to `graph --node`. Best and cheapest path available. |
| **3–25** | Describe the thing in prose. Adding the identifier costs 20–150% more tokens for no accuracy gain. |
| **≥ 26** | **Leave the identifier out of the query.** Including it costs 41 points of hit@1 and quadruples the bill. |

### Why

The score bonus for exact graph identity is **flat**. Nine documents sharing an identifier
all receive it equally, so the one you want is never separated from the other eight. A
common asset id like `artifact_root` pulls in every document that touches it.

### Recognising the failure while it happens

If a search returns 5+ results that are all `s-inputs` or all the same section type from
unrelated documents, and `graph_route.max_returned_identifier_specificity` is 1 — you hit
this. Re-ask in prose without the identifier.

---

<a id="s-4-an-exact-string-in-files-is-rg-s-question"></a>
## 4. An exact string in files is rg's question

> Capsule: Locating the single document that defines a rare qualified symbol:

Locating the single document that defines a rare qualified symbol:

| Surface | Tokens |
|---|---:|
| `rg -l` | **139** |
| `graph --node` | 1,128 |
| `search` with the identifier | 8,905 |

When you hold the exact string and only want *which files contain it*, `rg -l` is 8×
cheaper than the graph and 64× cheaper than search. The other two are not wrong — they
answer a larger question you did not ask.

**Switch away from `rg` the moment the question stops being "which files contain this
text".** Direction (who calls whom), typed relations, contracts, drift records, and
anything two hops out are not in the text and cannot be grepped.

---

<a id="s-5-one-hop-is-cheap-anywhere-two-hops-only-in-the-graph"></a>
## 5. One hop is cheap anywhere; two hops only in the graph

> Capsule: Neighbourhood of one header file:

Neighbourhood of one header file:

| Surface | Tokens | Reach |
|---|---:|---|
| `rg -l` | **912** | 1 hop, no direction |
| `graph --node` | 1,296 | 1 hop, typed and directed |
| `graph --chase --max-hops 2` | 21,459 | 2 hops, bounded, deduplicated |
| `rg` for hop 2 | *n* queries | one per node from hop 1; cost unknowable in advance |

**1 hop:** use `rg` or `--node`, they are within an order of magnitude.
**2+ hops:** `--chase` is the only surface that bounds the walk, reports `edge_total`
against `edge_read` per hop, and tells you when it truncated.
**Set `--max-hops` deliberately:** 2 hops already costs 16× what 1 hop does.

---

<a id="s-6-the-source-escalation-is-a-fixed-three-step-chain"></a>
## 6. The source escalation is a fixed three-step chain

> Capsule: Three things that will cost you a retry:

```
search "<question>"                                   -> routing_receipt.routing_receipt_id = SRR_…
source-permit --routing-receipt SRR_… --purpose … \
              --path … --pattern … --reason …         -> SIP_…  (TTL, frozen file snapshot)
source-search --permit SIP_…                          -> matching lines + SIR_…
```

Three things that will cost you a retry:

1. **One receipt buys one permit.** A second `source-permit` on the same `SRR_` is refused
   with *routing receipt has already been exchanged*. Each escalation needs its own search.
2. **Paths are relative to `inspection_root`, not to the workspace.** With
   `inspection_root: ".."`, a workspace document is `kairos_workspace/code/foo.md`, not
   `code/foo.md`. The error says "root-relative" without naming the root — check
   `.kairos/config.json`.
3. **`source-search` is a literal line grep.** In a governed document the TOML header
   repeats the section vocabulary, so a pattern like `root cause|resolution` matches the
   header's own `facets` and `question` fields before reaching the prose. Pattern on words
   that occur *only* in the body.

---

<a id="s-7-inspection-root-is-a-wall-not-a-preference"></a>
## 7. inspection_root is a wall, not a preference

> Capsule: KAIROS reads only what is promoted, and source-permit reaches only inside

KAIROS reads only what is promoted, and `source-permit` reaches only inside
`inspection_root`. A path outside it is refused at every purpose:

```
source scope paths must be non-empty and root-relative
```

A live source tree elsewhere on disk, an artefact tree from a completed run, a governance
document in another repository — **none of these are reachable by any KAIROS surface.**

This is not a gap to route around. It is the edge of what the governance covers. Reading
beyond it belongs to filesystem tools, and the two do not compete because they cannot both
answer. If your task spans that boundary, you will use both, and the split is decided by
where the bytes live.

---

<a id="s-8-reading-an-empty-result"></a>
## 8. Reading an empty result

> Capsule: An empty result is **not** automatically a finding. --node, --asset and --predicate

An empty result is **not** automatically a finding. `--node`, `--asset` and `--predicate`
attach a `no_match` block that separates the two cases:

| `no_match.reason` | Means | Do |
|---|---|---|
| *no row names this identity exactly* | The graph has it under a qualified name. `candidates` lists them. | Retry with a candidate |
| *no qualified form ends in it either* | The corpus genuinely does not mention this name | **This one may be cited as a fact** |
| *no relation carries this predicate* | Predicates are a closed set; `declared` lists the valid ones | Pick from `declared` |

Only the second is evidence about the corpus. Never report the first as "not in the graph".

---

<a id="s-9-anti-patterns"></a>
## 9. Anti-patterns

> Capsule: | Do not | Because | Instead |

| Do not | Because | Instead |
|---|---|---|
| Start with `search` by default | It cannot count, cannot reach the filesystem, and is the most expensive locator | Classify with §1 first |
| Put an exact document ID in a search query to *find* something | The exact-identifier bonus pins the entire result set to that one document — 10 sections of one file | Prose to find; the ID only to open a document you already chose |
| Paste a symbol name into a query without checking `df` | At df ≥ 26 it costs 41 points of hit@1 and 4× the tokens | `--resolve` first, then §3 |
| Loop `--artifact` to build a corpus total | 2,166× the cost of `--census` | `--census`, then `--inventory` |
| Raise `--candidate-limit` when the answer is *missing* | It scores more rows; it does not reach documents the query cannot match | Re-ask in different words |
| Treat an empty `--node` as "not in the corpus" | Matching is exact; qualified names miss | Read `no_match` (§8) |
| Request a `source-permit` outside `inspection_root` | Structurally refused | Use filesystem tools (§7) |

---

<a id="s-10-confidence-of-each-claim"></a>
## 10. Confidence of each claim

> Capsule: | Claim | Basis |

| Claim | Basis |
|---|---|
| §1 costs, §2 ratios, §3 df table, §4, §5 token figures | **measured** — reproducible on this corpus. §1 figures are medians over 30 real calls per surface; the rest are single passes |
| §6 chain order, §7 boundary, §8 semantics, "search cannot aggregate" | **structural** — follows from construction; no measurement changes them |
| The exact df cut-offs (2 / 25) | measured **on this corpus** — expect them to move with corpus size; the monotone direction is the durable part |
| Where BM25 sits | **structural only.** BM25 is a text ranker: no aggregates, no relations. KAIROS's section search is FTS5 with extra scoring on top, so "BM25 vs KAIROS search" is the same ranker family with and without the graph layer. |
| Whether KAIROS's extra scoring beats plain FTS on prose queries | **unmeasured.** On prose questions the text ranker does the work, and that split has not been isolated. Do not claim the graph layer pays for itself on prose. |

One caveat on §3: the prose arm used each target document's own capsule with the
identifier's tokens removed — prose in the corpus's own vocabulary, the best case. Your
own wording will do worse. The comparison between arms is fair; the absolute level of the
prose column is optimistic.

---

<a id="s-related"></a>
## Related

> Capsule: SEARCH_INDEX.md — what every surface returns, flag by flag

- `SEARCH_INDEX.md` — what every surface returns, flag by flag
- `OPERATIONS.md` — the session flow these surfaces sit inside
- `INGESTED_DOCUMENT_SPEC.md` — how identity is formed, and why matching is exact
- `LLM_OPERATING_CONTRACT.md` — the obligations on the caller
