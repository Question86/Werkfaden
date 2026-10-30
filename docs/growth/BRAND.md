+++
schema = "kairos-context/v1"
id = "KAIROS_BRAND_IDENTITY"
type = "documentation"
revision = 1
state = "active"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_BRAND_IDENTITY"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Recorded public naming and attribution decision, with its stated screening limitations."
claim_boundary = "Owns the recorded naming decision, not trademark clearance, legal advice or measured user preference."
entities = ["KAIROS", "KAIROS_BRAND_IDENTITY"]
facets = ["framework-documentation", "reference"]
criteria = []
does_not_answer = ["live project state", "unverified current release acceptance"]
distribution_role = "reference"
origin_commit = "6d8d498c62c411ecad9c49e1ab41e2a72b724e56"
origin_source_sha256 = "32f3b926bfdca650421b42550b67319d610168235982bc677d037a585a68b2da"

[[answers]]
intent = "procedure"
question = "Which public identity and attribution were selected for Werkfaden?"
target = "s-identity"

[refs]
current_rules = "[ref:AGENTS.md#s-overview|id:KAIROS_FRAMEWORK_RULES|v:dynamic|rel:requires|tags:current,authority|src:framework]"

[[search_contract]]
query = "Which public identity and attribution were selected for Werkfaden?"
expected = "KAIROS_BRAND_IDENTITY#s-identity"
required_top_k = 5
+++
# Werkfaden brand decision

## CONTEXT INDEX

- [Identity](#s-identity)
- [Why not keep KAIROS](#s-why-not-keep-kairos)
- [Screening actually performed](#s-screening-actually-performed)
- [Voice and recognition](#s-voice-and-recognition)
- [Compatibility boundary](#s-compatibility-boundary)


Decision date: 2026-09-10. Status: selected public rollout identity following preliminary collision screening; not a trademark clearance or a registered mark.

<a id="s-identity"></a>
## Identity

> Capsule: Records the Werkfaden name, descriptive positioning, signature and author attribution.

- Public name: **Werkfaden**. Plain-text identifier: `werkfaden`.
- Descriptor: **Project context and controlled changes for AI coding agents.**
- Signature: **Keep the thread. Verify the change.**
- Attribution: **Built by Yannick Wende.**

The intended association is German *Werk* plus *Faden*: the work and the thread connecting its parts. The name fits continuity across tasks and sessions without promising perfect memory or autonomous correctness. Approximate pronunciation for an English-speaking audience: VEHRK-fah-den. Use the normal spelling, with no umlaut substitution or clever capitalization.

This is a positioning decision, not evidence that users already prefer the name. Pronunciation and recall still need real first-reader testing.

<a id="s-why-not-keep-kairos"></a>
## Why not keep KAIROS

> Capsule: Records the naming rationale and the neighboring project considered during the original screening.

A direct neighboring project already presents itself as [Kairos: The Context Keeper](https://github.com/turtir-ai/kairos-context-keeper), with a pitch about context engineering and persistent project knowledge for AI development. That is a relevant discoverability collision, not simply another company in an unrelated industry. The earlier GitHub check also found multiple Kairograph repositories. Neither name is used as the main public brand in this rollout.

<a id="s-screening-actually-performed"></a>
## Screening actually performed

> Capsule: Lists the historical screening checks and explicitly limits what those observations establish.

| Surface | Query or check | Observed result | What it does not establish |
|---|---|---|---|
| GitHub repository search | `werkfaden` | No repositories returned before publication of this rollout | Unindexed/private uses, account availability or legal rights |
| GitHub repository search | `"werk-faden"` | No repositories returned | All spelling or sound-alike collisions |
| General web | `"Werkfaden"`; exact name plus software, AI, framework, brand/company terms | Literary uses surfaced; no identifiable competing software product surfaced in the checked results | Worldwide exclusivity |
| Indexed package search | Exact name scoped to PyPI, npm, crates.io and Hugging Face | No relevant package result surfaced | Package-name reservation or authoritative registry availability |
| Direct registry reads | PyPI JSON, npm registry and crates.io API name paths | Browser fetch errors; inconclusive | Do not reinterpret an error as an available name |
| Indexed social search | Exact name scoped to X, Bluesky and LinkedIn | No relevant exact-name product result surfaced | Handle availability or ownership |
| Domains | No registrar or authoritative registration check completed | Unverified; nothing purchased | Domain availability |
| Trademark registries | No completed exact/similarity clearance | Unverified; nothing registered | Legal clearance |

Search is a dated observation, not a durable guarantee. The checked non-product uses include [Filmfriend's film description](https://www.filmfriend.at/de/movies/denk-ich-an-deutschland-in-der-nacht) and literary criticism. They are not competing developer products and are not endorsements.

Other German-inspired candidates screened on the general web were Spuranker, Kernfuge, Fadenhalt, Taktfuge and Werklot. Werkfaden was selected for the clearest connection to project continuity and the founder's working voice, not from an invented numerical score. No equivalent registry clearance is claimed for those alternatives.

Before significant spend on domains, promotion or registration, complete name/handle reservations and appropriate trademark similarity checks. [DPMA explains its register and search scope](https://www.dpma.de/recherche/dpmaregister/); [its trademark guidance](https://www.dpma.de/marken/) also distinguishes filing from checking earlier rights. Do not use a registered-mark symbol.

<a id="s-voice-and-recognition"></a>
## Voice and recognition

> Capsule: Requires concrete observed claims and descriptive language without fabricated savings, users or reliability.

Use concrete relationships, source paths and observed outcomes. Keep the dry, descriptive tone. Avoid superhero language, generic agent hype, invented token savings, fabricated testimonials and claims of guaranteed growth.

Use one wordmark spelling, the same descriptor and the same signature across README, social bios, screenshots and demos. Visual direction: restrained typography and a continuous thread with distinct checkpoints. A screenshot is evidence, not a decorative dashboard; label the command, release and actual result. Do not fabricate terminal output. No finished logo or visual-recall test is claimed by this commit.

<a id="s-compatibility-boundary"></a>
## Compatibility boundary

> Capsule: Presentation naming does not rename engine packages, schemas, artifact identities or historical tags.

Only the public presentation changes. Do not rename Python packages, CLI commands, database/schema names, KAIROS artifact IDs, Workshop paths or historical tags. A one-line 'formerly KAIROS' reference explains old screenshots and commands; it is not a second competing brand.

The repository rename was verified on 2026-09-10: [Question86/Werkfaden](https://github.com/Question86/Werkfaden) retains repository ID `1320481505`. The old `Question86/KAIROS-GOLDEN` API route returns `Moved Permanently` to that same repository ID. This confirms the repository identity and API redirect; it is not an exhaustive test of browser, Git transport, raw-file or artifact redirects. New public links use the canonical Werkfaden address, not a redirect or the growth branch. This rename does not establish trademark, domain or social-handle clearance.

Public presentation lives in `.github/README.md`; GitHub [prioritizes that location](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes) without changing the root README admitted to the framework database.
