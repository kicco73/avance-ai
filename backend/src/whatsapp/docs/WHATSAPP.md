# WhatsApp channel (Meta Cloud API)

WhatsApp is a chat client of the same live sessions the rest of the
system holds. It is a **channel, not a turn engine**: it converts what a
person sent into a Bus message, and the frames that come back into
WhatsApp messages. It never runs a turn, never resolves a session by
hand and never reads a transcript.

Three properties define it, and the rest of this page is their
consequences.

1. **It never writes the first message.** `session.opened` — "this
   conversation has just been opened and has said nothing" — has no
   listener in this package. A phone is not written to until its owner
   writes. What the state *owed* is still owed and goes out with the
   answer to the first thing the person actually says
   (`TurnService.prepare_user_initiated_turn`, published as an
   `output.text` before the reply).

2. **The socket is a webhook, and it is opened in `listen()`.**
   `WhatsAppService.listen(controllers)` subscribes to what comes back
   and appends the controller Meta POSTs to. The webhook belongs to this
   package: leave `backend/src/whatsapp/` out of a build and there is no
   route for Meta to call and nothing that says there ever was one.

3. **It answers the person, it does not stream to a screen.** Only
   `output.text`, the choices and the notices are subscribed to.
   `output.text_stream`, `output.tool`, `state.changed` and
   `session.messages` are not.

## What it says and hears on the Bus

`docs/BUS.md` is the vocabulary; this is what this package does with it.

**Publishes** `session.enter`, `session.create`, `input.text`,
`input.button` and `input.audio` — a voice note posts the audio as a
callable, so nothing is fetched for a message no decoder in this build
will read.

**Subscribes** to `output.text`, `state.buttons`, `output.speech` and
`output.error`, and to nothing else. `output.text_stream`,
`output.tool`, `state.changed` and `session.messages` are a screen's
business. It subscribes to `output.text` twice, for two different things:
the frames of its own conversations, and (**only**) the one with no
`session_id` — a message belonging to no conversation, which this channel
has to carry itself.

**Does not** subscribe to `session.opened`, which is what "never writes
the first message" is made of.

**Contributes** `want()` at `turn.spoken_reply` when the exchange is to
be answered by voice, its own section of `config.services`, and its
controllers at `http.controllers`.

## What one message does

```text
Meta ──POST /api/skills/whatsapp/webhook──▶ WhatsAppController   (role=None, HMAC-verified,
                                         │                       answers 200 immediately,
                                         │                       drops redeliveries)
                                         ▼ background task
                                  WhatsAppService.receive
                                         │  Identities.of(): number → account, or a refusal
                                         ▼
                                  Conversation.receive
                                         │
              session.enter ────────────▶│  BUS ──▶ turn/input_listener.py
              session.info  ◀────────────┤          ← which conversation this is
             (session.create if that
              session is another
              channel's)
                                         │
              input.text / input.button / input.audio ──▶ BUS
                                         │
              output.text, state.buttons, output.speech, output.error ◀──
                                         ▼
                                  WhatsAppCloudApiClient
```

`origin_id` is the sender's number, and it is what routes an answer back:
`WhatsAppService._mine()` looks the conversation up by it. A frame with
no origin of ours but a `session_id` one of our conversations holds goes
there instead — the Bus's own "answer the asker, announce to the watcher"
rule.

## Which conversation

Every inbound message enters first (`session.enter {session_type: live}`,
with the sender's own active project), and the `session.info` that comes
back says which session it is and **whose channel it is on**. A session
open on another channel is taken over by asking for a new one
(`session.create`), which closes it with `channel-switch`. A live session
belongs to one channel for its whole life (`CoreSession.channel`), so
continuing here means continuing in a new session, never writing into
another channel's transcript.

Taking it is the right half of the rule *because* the entry was caused by
somebody writing: whoever the person **acts** on wins. A client whose
entry fires on its own — every reload, every reconnection — must stand
down instead, or the two would rally the session between them with nobody
having written anything.

The consequence is worth saying plainly: alternating between the phone
and another client gives one session per alternation. That is what
`channel-switch` has always meant — a transcript is never interleaved —
and it is the price of the guarantee, not a bug in it.

Entering answers with the state's choices too. They are remembered, never
sent: a phone is not a screen being refreshed, and only what an exchange
itself offers goes out (`_Entering` / `_Answering` in `conversation.py`).

A refusal comes back as `session.blocked` and each reason has a sentence
here: `paused`, and `terms` — which is not a sentence but the project's
own `legal/terms.md` with a single Accept button. Tapping it calls
`TurnService.accept_legal_terms` and confirms; the conversation itself
starts when the person writes again.

## The choices a state offers

WhatsApp interactive messages need a body, so the choices ride on the
last message of the exchange: `state.buttons` is held, and the
`output.text` published after it — the reply, and the end of the exchange
— carries them. 1–3 become reply buttons, 4–10 a list (only the first 10,
with a warning), titles truncated to 20/24 characters and
`ui_description` to 72; a body over 1024 characters is sent as text and
the choices follow on a short prompt.

An exchange that produces **no** message — a manual action whose
transition says nothing — would leave those choices with nothing to ride
on, so they go out on their own "What would you like to do?" prompt
instead. That flush is deferred by one event-loop turn
(`_flush_when_quiet`), which is what tells "no reply is coming" from "the
reply is next in the same queue": frames of one exchange are drained in
order by a single task and nothing yields between them.

## What each failure sounds like

The sentence is this channel's (`whatsapp/notices.py`).
`state_not_chat` and `action_unavailable` keep the choices — a notice
that says "use an action instead" had better come with actions; the rest
do not. There is no retry on a closed or vanished session: every message
enters before it is sent, which *is* the recovery.

## Identity

Meta's webhook carries no cookie, so the sender's number (E.164 digits,
no `+`) is looked up against `User.whatsapp_phone_number` — linked either
from the profile page's WhatsApp field
(`PUT /api/core/auth/me/phone-number`) or by registering straight from
WhatsApp: a number with no `User` row that sends text is treated as
attempting registration, and `AuthService.register_via_whatsapp`
validates the last word of it as an invite code through the same
`InviteManager.validate_for_registration` every other registration uses,
so a bad or expired code gets identical wording. On success the row is
created with `id`/`whatsapp_phone_number` both set to the number,
`provider="whatsapp"`, and the invited project becomes its active
project.

A registration is confirmed and nothing else: the invite code was not
something to answer, and this channel does not speak first.

Nothing is impersonated here. The conversation stamps `username` on every
message it publishes and the core re-establishes the context from it
(`WebSession.for_sender`). The only two calls this package makes directly
are the terms ones, and they run inside `WebSession.for_sender`.

## Voice

- **In**: a voice note is published as `input.audio` with the audio as a
  *callable*, so nothing is downloaded for a message no decoder in this
  build will read. Whoever transcribes converts it to `input.text` on the
  same envelope, which is what the core runs — this package does not take
  the transcript and re-publish it. It only watches whether a conversion
  happened: `publish_with_bounceback` tells it nothing decodes audio here
  ("I can't listen to voice notes yet"), and a decoder that produced
  nothing is the other notice ("I couldn't make out that voice note").
- **Out**: `voice-replies` decides — `when-spoken-to` (default), `always`,
  `never` — and the two answers are two objects (`TextReply` /
  `VoiceReply` in `whatsapp/replies.py`), chosen once per exchange. A
  `VoiceReply` says `want()` at `bus.POINT_SPOKEN_REPLY`, starts
  synthesizing the moment the turn announces the spoken text
  (`output.speech`, see `whatsapp/voice_notes.py`), and sends the MP3 in
  place of the text. Every way that can fail — nothing speaks, no audio
  text, a silent generation, an encoding or upload failure — falls back
  to the written reply, never to silence. Notices are never spoken.
- MP3 rather than OGG/Opus so WhatsApp shows an audio message rather than
  a voice note (`whatsapp/audio.py`, PyAV — no ffmpeg binary).

## Proactive sends

`task.whatsapp(phone_number, message_md)` publishes an `output.text` that
names a recipient and **no conversation** (`session_id` `None`, `channel`
`whatsapp`). That is the one `output.text` this channel carries itself
(`_unsolicited`): filtering on the channel alone would not do, since a
turn started here carries `channel: whatsapp` too. `False` means nothing
in this build carried it. Once sent,
`TurnService.record_unsolicited_reply` gives the message a home in that
recipient's transcript; it runs no automaton, and a failure there is
logged, never turning a successful send into `False`.

## Files

- `skill.py` — the skill, and nothing else.
- `whatsapp_service.py` — the service in both its forms (`WhatsApp` /
  `NoWhatsApp`, `installation()`): what it subscribes to, the
  conversations it holds, and the messages it carries for somebody else.
- `conversation.py` — one sender: entering, publishing what they said,
  and turning what comes back into messages.
- `webhook.py` — Meta's inbound wire: the envelope, the HMAC, the
  redeliveries, and each kind of message as the thing it is
  (`TextMessage`, `ButtonPress`, `VoiceNote`).
- `identity.py` — number → account, registration included.
- `notices.py` — every sentence this channel says for itself.
- `replies.py` — how one exchange answers: written or spoken.
- `outbound.py` — every limit the Cloud API puts on a message.
- `inbound_voice_note.py` — a voice note, posted as `input.audio`.
- `voice_notes.py` — the reply's `[audio]` text as MP3 bytes.
- `markdown.py` — CommonMark → what WhatsApp renders.
- `cloud_api_client.py`, `audio.py` — the transport and the codec.
- `whatsapp_controller.py` — the two webhook routes, under `/api/` so
  `nginx.conf` needs no change.
- `config.py` — the `whatsapp-service` section.
- `tests/` — the channel driven message by message against a fake Cloud
  API and the **real** `TurnInput` behind the Bus
  (`test_whatsapp_channel.py`), Meta's wire
  (`test_whatsapp_webhook.py`), voice (`test_whatsapp_voice.py`), and the
  whole thing against the real core (`test_whatsapp_flow.py`).

## Constraints worth knowing

- Meta expects 200 within a few seconds and retries otherwise: the route
  acks first and the message is handled in a `BackgroundTasks` job. The
  answer is written later still — the core runs the turn in a task of its
  own — which is why a test has to wait for what the Bus started (see
  `whatsapp_helpers.answered`).
- Free-form messages are only allowed within 24h of the user's last
  message; anything proactive outside that window needs approved
  templates.
- The token shown in Meta's "API Setup" expires in 24h — use a permanent
  System User token in `.config.yml`.
- `graph-version` defaults to `v23.0`; bump it when Meta deprecates it.
- Media ids from `upload_media` are valid 30 days and not reused.

## Meta setup (once)

1. developers.facebook.com → Business app → add the WhatsApp product.
2. *API Setup*: note the **Phone Number ID** (the test number is fine to
   start; add your phone among the test recipients).
3. Business Manager → System User → a permanent token with
   `whatsapp_business_messaging`.
4. *App settings → Basic*: **App Secret**.
5. *WhatsApp → Configuration → Webhook*: URL
   `https://<host>/api/skills/whatsapp/webhook`, your `verify-token`,
   subscribe to **messages**. The backend must already be up with
   `enabled: true` — Meta does the verification GET on save.
