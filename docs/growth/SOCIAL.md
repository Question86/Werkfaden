+++
schema = "kairos-context/v1"
id = "KAIROS_PUBLIC_SOCIAL_DRAFTS"
type = "documentation"
revision = 1
state = "ready"
authority = "architecture_authority"
workspace = "KAIROS_FRAMEWORK"
route = "KAIROS_FRAMEWORK/KAIROS_PUBLIC_SOCIAL_DRAFTS"
updated_at = "2026-10-30T01:33:38Z"
capsule = "Retained historical social-copy drafts and their original no-scheduling/no-automatic-publication boundary."
claim_boundary = "Draft text is source material, not a sent message, scheduled campaign, current feature verification or permission to post."
entities = ["KAIROS", "KAIROS_PUBLIC_SOCIAL_DRAFTS"]
facets = ["framework-documentation", "historical-evidence"]
criteria = []
does_not_answer = ["live project state", "unverified current release acceptance"]
distribution_role = "historical_evidence"
origin_commit = "6d8d498c62c411ecad9c49e1ab41e2a72b724e56"
origin_source_sha256 = "4a61abeda15e10dbd7951c3d92bca3954ca285f4673ab5ab9d410205e4bda8fd"

[[answers]]
intent = "historical_evidence"
question = "Where are the historical social drafts and their publication limits?"
target = "s-x-launch-post"

[refs]
current_rules = "[ref:AGENTS.md#s-overview|id:KAIROS_FRAMEWORK_RULES|v:dynamic|rel:requires|tags:current,authority|src:framework]"

[[search_contract]]
query = "Where are the historical social drafts and their publication limits?"
expected = "KAIROS_PUBLIC_SOCIAL_DRAFTS#s-x-launch-post"
required_top_k = 5
+++
# Social launch copy

## CONTEXT INDEX

- [X launch post](#s-x-launch-post)
- [Bluesky launch post](#s-bluesky-launch-post)
- [LinkedIn opening](#s-linkedin-opening)
- [Profile copy](#s-profile-copy)
- [Screenshot and recording brief](#s-screenshot-and-recording-brief)
- [Distribution discipline](#s-distribution-discipline)


Historical source material captured from commit `6d8d498c62c411ecad9c49e1ab41e2a72b724e56`. Statements about release, rollout, branch or tests below belong to that source epoch. Current operating contracts and verified current receipts own current behavior. This material does not authorize publication or future execution.

Status: drafts saved in GitHub. Nothing on this page is scheduled or published automatically. Use one public name, one inspectable pointer and one invitation. Keep personal accounts under Yannick's name; the product rename is not a persona reset.

<a id="s-x-launch-post"></a>
## X launch post

> Capsule: Preserves an X launch draft as historical copy, not technical validation evidence.

Today the framework gets a name that doesn't need a paragraph to distinguish it from someone else's AI project.

Werkfaden. The thread through the work.

The idea hasn't changed: give a coding agent a project it can query, rather than another story it has to remember.

Code is represented in source-linked Markdown. Project context is searchable. Changes to governed files go through the Workshop, with the code and its context checked together before the transaction is accepted.

That matters when a project outlives a conversation. A new session should be able to find the current task, follow the relevant references and work from the recorded state instead of reviving an old workaround with great confidence.

The engine is still KAIROS under the hood. Same commands, same pinned release. This is a clearer name, not a new model or a claim that tests have abolished bugs.

Keep the thread. Verify the change.

Try the first framework query and tell me where the explanation stops making sense:
https://github.com/Question86/Werkfaden

#AIEngineering #ContextEngineering

<a id="s-bluesky-launch-post"></a>
## Bluesky launch post

> Capsule: Preserves a Bluesky launch draft and its editing markers.

<!-- bluesky:start -->
KAIROS gets a new public name: Werkfaden. Project context and controlled changes for AI coding agents. Same engine, less naming confusion. Keep the thread. Verify the change. https://github.com/Question86/Werkfaden
<!-- bluesky:end -->

<a id="s-linkedin-opening"></a>
## LinkedIn opening

> Capsule: Preserves a LinkedIn introduction draft about project-context continuity.

I have been building a framework around a practical problem in AI-assisted development: the code changes, the conversation moves on, and the model's understanding of the project can become a separate version of reality.

Werkfaden is my attempt to keep those apart less often: source-linked Markdown, searchable project context and controlled Workshop transactions for governed changes. Formerly presented as KAIROS; the engine and release identities remain unchanged.

The useful question is not whether an agent sounds like it remembers. It is which source supports its next action, and how the resulting change is checked.

The repository now has a first-use guide and a claim-by-claim evidence page. The current license is for private, non-commercial use; professional evaluation requires written permission. I am looking for concrete first-use feedback, not applause for a diagram.

https://github.com/Question86/Werkfaden

<a id="s-profile-copy"></a>
## Profile copy

> Capsule: Preserves proposed product and personal profile copy without recording publication.

Product descriptor: Project context and controlled changes for AI coding agents.

Personal bio candidate: Art, photography, project management. Now building Werkfaden: queryable project context and controlled changes for AI coding agents.

<a id="s-screenshot-and-recording-brief"></a>
## Screenshot and recording brief

> Capsule: Defines the proposed demonstration evidence and avoids equating prompt rendering with an LLM call.

Show the pinned engine version, the actual framework-search query and its returned source/section. Then show one real candidate-to-postcheck transition in a disposable demonstration project. Explain that `kairos_cli.py` is the retained executable name. Do not rename terminal output, fabricate counters, expose private project paths or make a compilation claim from a synchronization check.

Use a 45-75 second recording as an editing target, not a promised onboarding duration. One legible screen is better than a montage of unreadable terminal panels.

<a id="s-distribution-discipline"></a>
## Distribution discipline

> Capsule: Records the original destination and identity-check epoch; future publication still requires explicit authority.

The canonical public destination is https://github.com/Question86/Werkfaden. The rename and repository ID were verified on 2026-09-10. Use this main project address for new launch posts and profile links, not the development branch. For technical evidence, link the exact release, commit or test run under the new repository address. The old API route reports a permanent redirect to the same repository ID; no exhaustive browser, raw-file, Git transport or artifact redirect test is claimed. Do not rewrite already published evidence or rename historical release artifacts for branding.

No bulk unsolicited messages, bought stars, engagement pods or mass tagging. Answer relevant technical discussions with a useful explanation first and disclose that you built the linked tool. Do not present an unmeasured benefit as a benchmark.
