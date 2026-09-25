# Token optimization

What a turn's request costs in input tokens, which part of it a provider
can serve from its prompt cache, and what stands in the way. Author
reference: nothing here is served to a product.

## What "N% from cache" measures

Manage services shows, per provider, `cache_read_tokens / input_tokens`
over the successful calls of the trailing 24h (`db/ai_usage.py`,
`cache_read_ratio`). Output tokens are not in the denominator. The
numerator is whatever the provider itself reports in the response's
usage block:

| Driver | Field read | Where |
| --- | --- | --- |
| Anthropic | `usage.cache_read_input_tokens` | `ai/_providers/anthropic_provider_v2.py` |
| OpenAI-compatible (OpenAI, Mistral, …) | `usage.prompt_tokens_details.cached_tokens` | `ai/_providers/openai_provider_v2.py` |
| Gemini | `usage_metadata.cached_content_token_count` | `ai/_providers/gemini_provider_v2.py` |

A provider that does not fill its field reads 0% here whether or not it
cached anything. Mistral fills it.

## How a turn's request is laid out today

`Prompt.to_system_prompt()` (`ai/turn/prompt.py`) splits the system
prompt in two:

- `stable` — every channel's definition and per-state content, plus the
  schema field order. Identical across consecutive turns in the same
  automaton state.
- `volatile` — the model's own memory block, and the env block
  `TurnProtocolUsingSchema.generate_reply` appends after it. Changes
  turn to turn even while the state does not.

After the system prompt comes the history: the priming exchange that
carries this turn's attachments (`ai/turn/priming.py`), then the
session's messages since the state's history cutoff, trimmed to the
input budget from the oldest end (`db/messages.py`,
`get_turn_history`).

Only Anthropic sees the split. `_build_system` puts one cache breakpoint
on `stable` and sends `volatile` as a second, uncached block. Gemini and
the OpenAI-compatible driver call `SystemPrompt.full_text()` and send
one system message, so on the wire the request reads:

```text
[system: stable + volatile] [priming attachments] [history 1 … n] [new user message]
```

Every implicit prefix cache (Mistral, OpenAI, Gemini) stops at the first
byte that differs from a recent request. `volatile` differs every turn
and sits before the history, so the cacheable prefix is `stable` alone.
The history — the part that grows only at its tail and would match the
previous turn byte for byte — is never cached. A ratio around 20% on
Mistral is, to a first approximation, the weight of `stable` in the
request.

## Why Gemini reads 0%

Gemini's implicit cache has a minimum prompt length below which nothing
is cached (1024 tokens for the Flash and Flash-Lite models, higher for
Pro). In the dev database every Gemini call of the last week sits under
that: mean input around 600 tokens, maximum around 1000. Nothing is
wrong with the count; it rises on its own once a project's `stable`
plus history crosses the minimum.

## The order that maximizes cache hits

Move `volatile` behind the history:

```text
[system: stable] [priming attachments] [history 1 … n] [new user message] [volatile]
```

Then the prefix `stable + attachments + history(n)` of turn n+1 is
exactly the whole request of turn n, and a turn pays full price only for
its new user message and the volatile tail. On a ten-turn conversation
that is well above 90% served from cache, against ~20% now.

Where the tail goes, per driver:

- OpenAI-compatible: a second `role: system` message after the history.
  OpenAI and Mistral accept a system message at any position.
- Gemini: a final `user` `Content`, or a trailing `Part` on the last
  user message.
- Anthropic: already caches `stable`. The remaining gain is a second
  breakpoint on the last history message, so the history is cached too
  (the docstring of `_build_system` names this as the next step, once
  measured).

Files: the two drivers above stop calling `full_text()`; the docstring of
`SystemPrompt` and the two "placed last" sentences in
`PROJECT_SPECS.md` (§4.3, §5.3) describe the new position.

### What still breaks the prefix, and stays broken

- **A state change.** `stable` is per state, and so is the tool catalog
  (`ai-may-read-sources` / `ai-must-read-sources`). The first turn in a
  new state is a full-price turn.
- **The history budget.** `get_turn_history` trims from the oldest end.
  The turn on which it starts dropping messages shifts the whole prefix;
  every turn after that shifts it again. A session that has hit its
  budget caches only `stable` and the attachments, whatever the order.
- **Attachments that differ between turns.** Signal attachments are
  added only on a turn that requests that signal (`PROJECT_SPECS.md`
  §6), so a signal that is evaluated intermittently changes the priming
  exchange and everything after it.
- **A different provider.** The cascade's failover sends the same text
  to a provider that has never seen it.

## Attachments: can they be stored once instead of sent every turn?

Not in the sense of putting them somewhere the model can read without
their tokens being counted. Every provider bills the tokens the model
attends to on every call; what a provider offers is a way to make those
tokens cheaper, or a way to not send them at all.

Today `build_priming_messages` inlines every applicable attachment as a
synthetic user/assistant exchange at the head of the history, on every
turn. Text extensions go in as literal text (`PROJECT_SPECS.md` §6).

What each provider can do with that:

- **Anthropic — prompt cache.** A breakpoint on the priming exchange
  (or, with the reorder above, the single breakpoint on the last history
  message covers it) makes the attachments a cache read on every turn
  after the first: same token count, cache-read price (a tenth of the
  base input price at the time of writing), for as long as the cache's
  TTL is kept alive by traffic. The Files API removes the re-upload of
  the bytes, not the tokens.
- **Gemini — explicit context cache.** `caches.create` stores system
  instruction plus content server-side and returns a handle the request
  references with `cached_content`. This is the closest thing to
  "memorize once": the attachments leave the request body. The tokens
  are still counted, at the cached-token price, plus a storage price per
  token-hour for the cache's lifetime (default TTL one hour, settable).
  Same minimum size as the implicit cache, so it does nothing for a
  project whose attachments plus prompt are under it. One cache per
  (project, revision, state) would be the natural key.
- **OpenAI, Mistral — chat completions.** Nothing is stored server-side
  across calls. The only discount is the implicit prefix cache, which
  the reorder above already exploits; on a cached hit the attachments
  cost the discounted rate but are sent and counted every turn.

The one way to take attachments out of the prompt on every provider is
to not send them: expose them the way sources already are, as a tool the
model calls when it needs the content. The mechanism exists
(`ai-may-read-sources` → `select_rows_*`, `PROJECT_SPECS.md` §4.2); an
equivalent `read_attachment(filename)` would make each attachment cost
zero on the turns that do not read it, one tool round on the turns that
do. The trade is real: the model no longer has the content in front of
it unless it asks, so an attachment that shapes every reply (a persona,
a rulebook) belongs in the prompt and in the cache, while reference
material consulted occasionally (a price list, a FAQ) belongs behind a
tool.

Prices and TTLs above are the providers' published ones at the time of
writing; check them before deriving a number from this page.

## What was spent, and who pays for it

Every model call writes one `AiUsage` row: input, output, thinking, cache
read and cache creation tokens, each in its own column, never folded into
one another. Prices differ per provider and per model, so the row carries
`provider_label` (`driver/model`) of the provider that actually answered —
when a cascade gives up on one provider and moves to the next inside the
same call, the tokens go under the one that replied.

A row is charged to an account, and the account decides its `kind`:

| kind           | charged to                                 | columns set                          |
| -------------- | ------------------------------------------ | ------------------------------------ |
| `session`      | a conversation: its turns, and the tasks, searches and reports it causes | `session_id`, `username`, `project_id` |
| `project`      | work on a project outside any conversation | `username`, `project_id`             |
| `unattributed` | a call made through a service no caller charged | none                            |

A skill may charge its own kind of work under its own `kind`. `session_id`
and the other identifiers are plain values, not references: deleting a
session does not delete what it cost.

A caller charges the service it holds with `charged_to(account)` and makes
its calls through what that returns; every call through it — including the
ones one call makes internally — lands on that account. A growing
`unattributed` total is a caller that was never charged.

`Db.get_ai_usage_totals(group_by)` sums the tokens and counts the calls,
grouped by any of `session_id`, `username`, `project_id`, `test_run_id`,
and always also by `kind` and `provider_label`, so each line can be priced
on its own.
