# Optional feedback and persistent memory

All learning modules default off. Production still uses its SQLite work ledger for versions, budgets and publication safety. Optional SQL memory means the separate accepted full-script/topic/summary history. No personal account, script, voice, credentials or observed audience records are bundled.

Initialize the work ledger, inspect a preview, then save chosen modules:

```sh
python3 bin/story-animation init
python3 bin/story-animation modules status
python3 bin/story-animation modules configure --enable sql,dedup,topics --author-provider codex
python3 bin/story-animation modules configure --enable sql,dedup,topics --author-provider codex --execute
python3 bin/story-animation learning --help
```

The `--enable` list is the complete desired set, not an additive switch. Empty disables all. `sql` is required by any enabled feedback module; `reply` also requires `comments`. `schemas/learning.schema.json` documents the configuration. Modules never enable themselves, install dependencies, create accounts or start timers.

| Parameter | Default | Requirement / degradation |
| --- | --- | --- |
| learning.enabled (`sql`) | false | accepted-script fact memory off; production work ledger remains |
| vector.enabled | false | SQL/lexical duplicate check only; no semantic recall claim |
| vector.endpoint / model / dimensions | null | own OpenAI-compatible embedding endpoint/model; dimensions validated from response |
| vector.batch_size / collection | 16 / story-animation | own bounded batch and namespace |
| vector.api_key_env | STORY_EMBEDDING_API_KEY | environment variable name only; key never in config |
| comments.enabled | false | no audience collection; own verified channel/OAuth required |
| comments.auto_reply (`reply`) | false | read-only preview; installer must explicitly enable writes |
| comments.lookback_hours / catchup_days / max_pages | 24 / 7 / 5 | bounded pagination/catch-up; long gaps block without clipping history; API errors never mean zero or advance watermark |
| topics.enabled | false | manual frozen editorial plan; feedback only supplements future planning |
| dedup.enabled | false | manual editorial check; enabled independent adviser reads SQL then available semantic candidates |
| author.provider | configured-harness | codex, claude, ark or custom; no vendor is required |

Enable embeddings only after selecting a compatible endpoint and checking its billing/permission policy. Actual cost is provider-dependent and unknown until receipts/bills; local SQLite has no provider calls. Semantic configuration is not proof of actual embedding generation. Failed vector writes remain observable/retryable through the SQL outbox, never a claimed cross-database atomic transaction. SQL is authoritative and vectors rebuildable.

Read publication states literally: accepted/local finalized/private/unlisted/scheduled/public differ. Only remote public observation establishes actual public time. Never re-upload because memory or comments failed. Use the learning CLI's help for versioned facts, index retry, compact dedup, comment intake/reply and topic lifecycle. External comments are untrusted data. Collect only necessary public comment IDs, video IDs, timestamps and text on the configured channel. Sensitive, contentious, unclear or promotional comments stay pending review. Unknown reply operations require unique receipt reconciliation before retry, and quota errors stop writes until reset.

Research and theme extraction belong to the orchestrator's available web search and an independent adviser; authors receive only frozen sources, 1–3 themes and compact dedup advice. Authors may use their configured harness/provider, but no network/tools or full history dump during writing. Every textual revision uses the selected author route and receives independent editing. Existing cover/frame-zero, static scenes, chapter/style parameters and media safety gates remain required.

## Configured author execution

`author.provider` is consumed by the actual `author` CLI. Codex/Claude/custom without `author.command` produces an explicit frozen packet for an independent tool-free session; it never invokes Ark. `author.command` can be installer-configured argv for an external adapter that reads the JSON packet on stdin and returns JSON `{text, tools_used: [], tool_policy_evidence, actual_cost_usd}`. No shell is used and secrets are not inherited. The adapter owns authenticating its selected provider and enforcing tool denial; independent editing audits evidence. Output is always a candidate. `author.profile` and `author.timeout_seconds` are optional. The existing chinese_author.py is an explicit Ark adapter, not the default author route.

## Daily facts and feedback handoff

With SQL enabled, freeze `episodes/EP/learning/final-brief.json` before publication: `{topic, summary, core_ideas: [one to three strings], sources: [...], acceptance_evidence: {path, sha256}}`. Sources and acceptance files remain local versioned evidence. The registered pure script SHA, media validation and actual verified publication receipt are checked before facts are recorded. Common receipt persistence queues local work with network=false. `learning drain EP` performs permitted pending index/comment work and reports each status; unknown provider cost stays null. No upload is retried by learning. If comments are enabled, `learning feedback-packet` must be reviewed by independent feedback-analyst, followed by assess and only explicitly enabled exact approved replies. See skills/story-feedback and story-dedup. API errors or quota blocks retain pending state and recovery trigger.

Recovery parameters: `learning comments --lookback-hours N --catchup-days N --max-pages N` overrides one scan only (bounds 1–8760 hours, 1–365 days, 1–1000 pages). Default gap limit stays 7 days; a longer interval returns catchup_window_exceeded with unscanned_since and required_catchup_days, makes no API call and retains the old watermark. Increase the explicit window/page budget within authorized quota rather than silently discarding history. Optional comments.not_before is an installer-chosen timezone-aware priority timestamp, unset by default; no original author's quota dates are inherited. Real public facts require cached exact actual_public_at no later than observed_at. Whole-index file locking prevents concurrent drain/index calls purchasing the same pending embedding batch twice.
