# WhatsApp channel (Meta Cloud API)

WhatsApp as one more chat client of the very same live sessions the SPA
uses — not a separate bot. An inbound text from a linked number becomes
`ChatService.process_turn` on that account's current live session (its
active project, its Terms acceptance, its history); every assistant
message the turn persisted goes back out through the Cloud API. Nothing
WhatsApp-specific is stored: a conversation started on WhatsApp shows up
in the web UI's Sessions panel, "Label sessions", metrics, etc. exactly
like one started in the browser.

```text
Meta ──POST /api/whatsapp/webhook──▶ WhatsAppController   (role=None, HMAC-verified,
                                         │                  answers 200 immediately)
                                         ▼ background task
                                    WhatsAppService.handle
                                         │  number → email (whatsapp-service.users)
                                         │  Session().impersonate(email)
                                         ▼
                        ChatService.get_or_create_current_session
                        ChatService.get_messages   (open_if_needed → opening message)
                        ChatService.process_turn
                                         │  new assistant rows since we started
                                         ▼
                                  WhatsAppCloudApiClient.send_text
```

## Files

- `whatsapp/whatsapp_service.py` — identity gate, turn orchestration, Markdown → WhatsApp flattening, dedup of Meta's redeliveries, per-sender ordering.
- `whatsapp/cloud_api_client.py` — `send_text` (auto-split over 4096 chars) and `mark_read`.
- `controllers/whatsapp_controller.py` — the two webhook routes, under `/api/` so `nginx.conf` needs no change.
- `config.py` — `WhatsAppServiceConfig` / `whatsapp-service` section (optional, default off; see `.config.example.yml`).
- `tests/test_whatsapp_channel.py` — contract tests with fake ChatService/Db/Cloud API.

## Identity

The login wall is cookie/JWT based and Meta's webhook never carries one,
so `whatsapp-service.users` maps each sender's number (E.164 digits, no
`+`) to a `User` row's email. The account must already exist — registered
from the web, Terms accepted, access to its active project — WhatsApp
never creates users or bypasses invites. Unlisted or unregistered numbers
get a fixed reply and nothing else. The turn runs under
`Session().impersonate(email)` with the row's own role, so every ownership
check downstream behaves as if that user were logged in.

Natural next step, deliberately not done here: a `User.whatsapp_number`
column plus a "link my number" action in ProfileView.vue, replacing the
config mapping. `migration-strategy: upgrade` would add the column.

## What the user sees

- Opening message (if the project generates one) and then the reply,
  each as its own WhatsApp message; a transition's follow-up messages
  (`action_prompt`, opening turn of the new state) come through too, since
  the channel sends whatever assistant rows the turn persisted.
- Non-chat/final state (`process_turn` → 409): a short notice pointing to
  the web. Manual actions aren't exposed on WhatsApp yet — mapping them to
  keywords or interactive buttons is a follow-up.
- Paused project / Terms pending: a notice, no turn.
- Audio/images: "text only" notice. (`ListenService` could transcribe
  voice notes later — the webhook delivers a media id to download.)
- Markdown: `**bold**`→`*bold*`, headings→bold lines, links spelled out,
  `*` bullets→`-`. Everything else WhatsApp already renders or ignores.

## Constraints worth knowing

- Meta expects 200 within a few seconds and retries otherwise: the route
  acks first, the turn runs as a `BackgroundTasks` job; redeliveries are
  deduplicated by `wamid`.
- Free-form messages are only allowed within 24h of the user's last
  message. Replies are always inside that window; anything proactive
  (`WakeupService`-style pushes) would need approved templates.
- The token shown in Meta's "API Setup" expires in 24h — use a permanent
  System User token in `.config.yml`.
- `graph-version` defaults to `v23.0`; bump it when Meta deprecates it.

## Meta setup (once)

1. developers.facebook.com → Business app → add the WhatsApp product.
2. *API Setup*: note the **Phone Number ID** (test number is fine to start; add your phone among the test recipients).
3. Business Manager → System User → generate a permanent token with `whatsapp_business_messaging`.
4. *App settings → Basic*: **App Secret**.
5. *WhatsApp → Configuration → Webhook*: URL `https://<host>/api/whatsapp/webhook`, your `verify-token`, subscribe to **messages**. The backend must already be up with `enabled: true` — Meta does the verification GET on save.
