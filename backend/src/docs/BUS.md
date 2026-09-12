# The Bus — eventi implementati

Ricavata dal codice (`bus.subscribe` / `bus.publish`), non dal disegno.
Aggiornare qui nello stesso commit che cambia il bus.

## Messaggi

| Tipo | Corpo | Pubblicato da | Ricevuto da |
| --- | --- | --- | --- |
| `input.text` | `{text}` — quello che la persona chiede | `system/bus_channel.py` (frame del browser), `whatsapp/turn_exchange.py`, `listen/decoder.py` (conversione da `input.audio`) | `turn/input_listener.py`, `whatsapp/inbound_voice_note.py` (iscritto per un solo scambio) |
| `input.audio` | `{audio}` — i byte, o una callable che li scarica | `whatsapp/inbound_voice_note.py` | `listen/decoder.py` |
| `output.text_stream` | `{text}` — un pezzo di un messaggio mentre viene scritto. **Vuoto** = la scrittura è cominciata e non c'è ancora niente da leggere | `turn/input_listener.py` | `webchat/webchat_service.py` |
| `output.tool` | `dict` — una tool call; `phase` distingue inizio e risultato | `turn/input_listener.py` | `webchat/webchat_service.py` |
| `output.reaction` | `{message_id, reaction}` — qualcuno ha reagito a **quel** messaggio | `turn/input_listener.py` | `webchat/webchat_service.py` |
| `state.changed` | `{state, new_state, triggered_action}` — solo **quando cambia**: chi legge tiene l'ultimo che gli è stato detto | `turn/input_listener.py` | `webchat/webchat_service.py` |
| `ui.buttons` | `{actions}` — le scelte offerte adesso. Chi mostra la chat apre i bottoni **appena arriva questo**, senza aspettare altro | `turn/input_listener.py` | `webchat/webchat_service.py`, `whatsapp/turn_exchange.py` |
| `input.button` | `{id}` — una di quelle scelte, presa. Stessa strada di `input.text`, così le due non si sorpassano | `system/bus_channel.py` (frame del browser) | `turn/input_listener.py` |
| `output.text` | `{text, assistant_message_id, timestamp}` — un messaggio intero. L'**ultimo** è la risposta, ed è quello che dice che lo scambio è finito. Il testo da pronunciare è un messaggio a parte (`output.speech`), non un campo di questo | `turn/input_listener.py`, `tracking/actuators/actuator_set.py` (`task.whatsapp`) | `webchat/webchat_service.py`, `whatsapp/whatsapp_service.py`, `whatsapp/turn_exchange.py` |
| `output.error` | `{message, detail, code}` — al posto della risposta: `code` dice cosa è successo, la frase la scrive chi parla alla persona | `turn/input_listener.py` | `webchat/webchat_service.py`, `whatsapp/turn_exchange.py` |
| `output.speech` | `{text}` — il testo da pronunciare: la versione parlata della risposta, che il modello scrive accanto a quella scritta. Arriva mentre la risposta è ancora in scrittura, e un annuncio successivo sostituisce il precedente — l'ultimo è quello salvato col messaggio | `turn/input_listener.py` (lo annuncia), `talker/ai_talker.py` (lo chiede) | `talk/skill.py`, `webchat/webchat_service.py`, `whatsapp/turn_exchange.py` |
| `output.audio_stream` | `{stream}` | `talk/skill.py` | `talker/ai_talker.py` (per un solo scambio) |
| `ui.notification` | `dict` | `tracking/wakeup_service.py`, `tracking/actuators/action_task.py`, `tracking/actuators/chat_namespace.py` | `system/bus_channel.py` |
| `ui.human_takeover` | `{session_id, project_id}` | `tracking/actuators/chat_namespace.py` | `system/bus_channel.py` |
| `ui.system_warning` | `dict` | `project/health_notifications.py` | `system/bus_channel.py` |
| `ui.progress` | `dict` | `system/broadcaster.py` | `system/bus_channel.py` |
| `tool.send_mail` | `{to, subject, body_md}` | `tracking/actuators/actuator_set.py` (`task.send_mail`) | `mail/skill.py` |

Il corpo di ogni messaggio è **un dizionario**. Sul filo il frame è quel
dizionario con il proprio `type` dentro: un solo modo di leggere, nessun
involucro.

## Una richiesta, una risposta

Non esistono turni: una persona chiede, qualcosa risponde. `input.text` è la
richiesta; il coalescer (`Db.unconsumed_user_fragments`, usato da
`TurnService._process_turn_body`) decide quante richieste diventano una
risposta sola. Quello che torna indietro:

```text
output.text_stream {text: ""}   ha cominciato a scrivere, niente di leggibile
output.tool                     sta usando uno strumento
output.text_stream {text: "…"}  i pezzi, man mano
output.text                     un messaggio che lo stato doveva prima di poter rispondere
output.reaction                 ha reagito a quello che la persona ha detto
state.changed                   solo se la conversazione si è spostata
ui.buttons                      cosa si può fare adesso
output.text                     la risposta — e lo scambio finisce qui
```

Non c'è un messaggio terminale a parte: **la risposta è la fine**. Quando
qualcosa va storto, al suo posto arriva `output.error`.

Chi legge non aspetta un payload finale, mette insieme quello che è stato
pubblicato — lo fanno allo stesso modo `chatClient.js` (`normalizeResult`) e
`conftest.chat_turn`, così un test legge quello che legge davvero un browser.

## Frame del websocket (`/api/core/bus`)

Non passano dal bus: sono del socket.

| Frame | Verso | Cosa fa |
| --- | --- | --- |
| `input.text` | browser → server | Quello che la persona scrive: l'unico tipo che un client può mettere sul bus (`CLIENT_INJECTABLE`) |
| `input.button` | browser → server | Un bottone è stato cliccato, col suo `id` — va sul bus come `input.text` (`CLIENT_INJECTABLE`) |
| `subscribe` / `unsubscribe` | browser → server | Registra i tipi che questa connessione vuole ricevere (`CLIENT_REGISTRABLE`) |
| `ping` / `pong` | browser ↔ server | Tenuta in vita |
| `human_prompt` | server → browser | Un turno aspetta una risposta da una persona. Va solo alle connessioni registrate |
| `human_reply` / `human_typing` | browser → server | La risposta dell'operatore e il suo «sto scrivendo» |
| `switched_to_other_client` | server → browser | Un'altra connessione della stessa identità ha preso il canale |

## Cosa una connessione può registrare

`CLIENT_REGISTRABLE` = `WEB_FORWARDED` (`ui.notification`, `ui.human_takeover`,
`ui.system_warning`, `ui.progress`) + `human_prompt`.

Un tipo fuori da quella lista viene rifiutato: registrarsi sarebbe altrimenti un
modo per leggere un tipo interno. La registrazione vive sulla connessione e va
ridichiarata a ogni riconnessione.

Registrarsi è **come una connessione dichiara cosa è**: `human_prompt` arriva a
chi l'ha chiesto e a nessun altro, ed è questo che rende una scheda quella che
risponde come persona. Un prompt già in attesa quando una connessione si
registra le viene consegnato in quel momento.

## Punti di contribuzione

Non sono messaggi: qualcuno chiede, in modo sincrono, e chi si è registrato
riempie la sua parte.

| Punto | Cosa si assembla | Chiesto da | Riempito da |
| --- | --- | --- | --- |
| `api.state` | il payload di `GET /api/core/state` | `system/api_state_controller.py` | `talk`, `listen`, `build` |
| `config.services` | la fotografia pubblica dei servizi | `config.py` | `talk`, `listen`, `mail`, `whatsapp`, `testing`, `build` |
| `http.controllers` | i controller da montare | `controller.py` | `talk`, `listen`, `webchat`, `whatsapp`, `avance_platform`, `testing`, `build` |
| `core.services` | il core composto | ogni skill | `main.py`, `testing` |
| `automaton.loader` | quale loader risponde «dammi questo automa» | `main.py` | `avance_platform`, `product` |
| `turn.spoken_reply` | `SpokenReply` — `want()` chi gestisce l'interfaccia, `ask()` chi sa parlare | `tracking/tracking_processor.py` | `talk`, `webchat`, `whatsapp` |
