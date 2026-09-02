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
                                         │  number → user_id (User.whatsapp_phone_number)
                                         │  Session().impersonate(user_id)
                                         ▼
                        ChatService.get_or_create_current_session
                        ChatService.get_messages   (open_if_needed → opening message)
                        ChatService.process_turn
                                         │  new assistant rows since we started
                                         ▼
                                  WhatsAppCloudApiClient.send_text
```

## Files

- `whatsapp/whatsapp_service.py` — identity gate, turn/action orchestration, manual-actions-as-buttons/list, Markdown → WhatsApp flattening, dedup of Meta's redeliveries, per-sender ordering.
- `whatsapp/cloud_api_client.py` — `send_text` (auto-split over 4096 chars), `send_buttons`/`send_list` (interactive replies), `send_audio`, `upload_media`/`download_media`, and `mark_read`.
- `whatsapp/audio.py` — WAV (as `TalkService` emits it, streaming header included) → OGG/Opus, the only format WhatsApp renders as a voice note. Encoder from PyAV, already installed as faster-whisper's dependency; no ffmpeg binary.
- `chat/chat_service.py` — `manual_actions` on every state payload reaching a client with a known session (`_with_manual_actions`); `automaton/automaton.py`'s `manual_actions_for` is the actual filter, shared with `tracking/wakeup_service.py`'s own cross-project notification push.
- `controllers/whatsapp_controller.py` — the two webhook routes, under `/api/` so `nginx.conf` needs no change.
- `config.py` — `WhatsAppServiceConfig` / `whatsapp-service` section (optional, default off; see `.config.example.yml`).
- `db/models.py` / `db/users.py` — `User.whatsapp_phone_number`, the phone → account link itself.
- `controllers/auth_controller.py` — `PUT /api/auth/me/whatsapp-phone-number`, ProfileView.vue's own save action.
- `auth/auth_service.py` — `register_via_whatsapp`, the WhatsApp-native signup path (shares `_register_with_invite` with the web's own `complete_registration`).
- `project/invites.py` — `whatsapp_url` on a created invite's payload, `ShareProjectDialog.vue`'s WhatsApp QR.
- `tests/test_whatsapp_channel.py` — contract tests with fake ChatService/Db/Cloud API.

## Identity

The login wall is cookie/JWT based and Meta's webhook never carries one,
so each sender's number (E.164 digits, no `+`) is looked up against
`User.whatsapp_phone_number` — a nullable, unique column an account gets
linked to one of two ways:

- An already-registered web user adds it themselves from ProfileView.vue's
  own "WhatsApp" field (`PUT /api/auth/me/whatsapp-phone-number`).
- A brand-new identity registers straight from WhatsApp: `ShareProjectDialog.vue`'s
  WhatsApp tab renders a `wa.me` QR (`project/invites.py`'s own `whatsapp_url`,
  built from `whatsapp-service.phone-number` + the invite code) that opens
  a chat to our business number with the invite code pre-filled as the
  message. A number with no `User` row at all that sends a text message is
  treated as attempting exactly that: `AuthService.register_via_whatsapp`
  validates it through the very same `InviteManager.validate_for_registration`
  the web's own `complete_registration` uses, so a bad/expired/maxed-out
  code gets back the identical wording either channel would show. On
  success the row is created with `id`/`whatsapp_phone_number` both set to
  the phone number, `provider="whatsapp"`, no email/name/picture — every
  Google-only field stays null — and the invited project becomes its
  active project directly (there's no separate "activate" step on
  WhatsApp the way the web's own post-registration boot has). A number
  that isn't attempting registration and isn't linked to anything gets a
  fixed "not linked" reply instead.

Either way, once linked, an unregistered number (row exists, but not
through a completed invite/Terms flow) gets a fixed reply and nothing
else. The turn runs under `Session().impersonate(user_id)` with the row's
own role, so every ownership check downstream behaves as if that user
were logged in — `user_id` is the row's `id` (email for a Google account,
the phone number itself for a WhatsApp-native one), not necessarily an
email at all.

## What the user sees

- Opening message (if the project generates one) and then the reply,
  each as its own WhatsApp message; a transition's follow-up messages
  (`action_prompt`, opening turn of the new state) come through too, since
  the channel sends whatever assistant rows the turn persisted.
- The current state's manual actions (`ChatService`'s own `manual_actions`
  — untriggerable actions, plus every action while a test session's
  auto-tracking is off) ride along on the *last* message of any reply
  that ends on a state with some: 1–3 actions become WhatsApp reply
  buttons, 4–10 become a list (more than 10: only the first 10, with a
  warning logged), title truncated to 20/24 chars for a button/list row
  (`ui_description` to 72). Tapping one applies it the same way a
  button click in ActionButtons.vue does (`ChatService.apply_manual_action`)
  — a stale button (tapped after the state already moved on) gets a short
  notice plus the *current* state's own buttons instead of an error; "a
  reply is already being generated" gets a "please wait" notice with no
  buttons attached; a transition producing no message of its own still
  sends an interactive message (the new state's `ui_label`, or "Done.")
  so the conversation is never left without controls. A state with no
  manual actions behaves exactly as before — plain text only. Same 24h
  free-form window as any other reply (see below) — buttons/lists are
  never sent outside it either.
- Non-chat/final state (`process_turn` → 409): a short notice pointing to
  the web.
- Paused project: a notice, no turn.
- Legal terms pending (a project's own `legal/terms.md`, distinct from the
  one-time platform Terms at registration): the terms content itself,
  same Markdown flattening as any other reply, with a single Accept
  button — the WhatsApp equivalent of TermsView.vue. Tapping it calls
  `ChatService.accept_legal_terms` and then bootstraps the session like a
  fresh registration would (a brand-new session's own opening message,
  if any; nothing extra for one whose prior history was already
  delivered — never a resend of it), falling back to a plain "terms
  accepted" confirmation when there's nothing new to send.
- Any other unexpected error (a bug, a DB error, an invite-redeem failure
  that isn't one of `AuthService`'s own `PermissionError` reasons, etc.):
  a generic "we apologize for the inconvenience" notice, never silence —
  `WhatsAppService.handle` wraps reply resolution and falls back to it.
- **Voice notes in**: downloaded from Meta (two hops: media node → short-lived URL, both with the Bearer), transcribed by `ListenService` (faster-whisper decodes OGG/Opus as is) and processed exactly like typed text — the transcript is what gets persisted as the user's message, so the web shows it too. No `listen-service` configured → "I can't listen to voice notes yet". Empty/failed transcript or download → "I couldn't make out that voice note". An unlinked number's voice note is never downloaded.
- **Voice notes out**: `voice-replies` in the config decides. Default `when-spoken-to`: a voice note back when the user sent one, text when they typed. `always`: every reply with an audio text goes out spoken; `never`: text only. The voice note is `TalkService.generate` for the reply's own `[audio]` text (`Message.audio_text`), re-encoded to OGG/Opus and uploaded; it *replaces* the text, it doesn't duplicate it. Whenever a voice note can't be produced — no `talk-service`, the project/state has talk disabled (no audio text), encoding or upload failure, silent generation — the text is sent instead. Channel notices (paused, terms, errors) are never spoken. Buttons need a text body: after a spoken reply they come on a short "What would you like to do?" follow-up.
- Images/documents/stickers: "text only" notice.
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
- Voice costs: transcription runs on the server CPU (Whisper `small` takes a few seconds for a 30 s note — fine, the webhook already answered 200 and the turn is in the background, but the user waits that much longer). Media ids from `upload_media` are valid 30 days and not reused; `TalkService`'s own cache already dedups the generation by text, only the upload repeats.

## Meta setup (once)

1. developers.facebook.com → Business app → add the WhatsApp product.
2. *API Setup*: note the **Phone Number ID** (test number is fine to start; add your phone among the test recipients).
3. Business Manager → System User → generate a permanent token with `whatsapp_business_messaging`.
4. *App settings → Basic*: **App Secret**.
5. *WhatsApp → Configuration → Webhook*: URL `https://<host>/api/whatsapp/webhook`, your `verify-token`, subscribe to **messages**. The backend must already be up with `enabled: true` — Meta does the verification GET on save.
