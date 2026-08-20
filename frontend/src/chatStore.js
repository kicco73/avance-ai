import { nextTick, ref } from 'vue'
import {
  getCurrentSession,
  postCreateSession,
  getCurrentTestSession,
  postCreateTestSession,
  getSessions,
  getTestSessions,
  deleteSession,
  getMessages,
  postAction,
  getAutoTracking,
  postAutoTracking,
  getAiModels,
  postAiModelSelection,
  messageAudioUrl,
  postListenTranscribe,
  postReset,
  postTruncateSession
} from './api.js'
import { sendMessage as sendChatMessage, onNotification } from './chatClient.js'
import { playMessageChime, playMessageAudio } from './audio.js'
import { runOnEnterScript } from './onEnterActions.js'
import { clearApiError } from './errorStore.js'

export const state = ref(null)
// The chat conversation's current session_id (see backend's
// ChatSessionManager) — null until the first loadMessages()/ensureSession()
// bootstrap. Every write call must carry it; the backend still resolves
// the true writable session itself and this is kept in sync from each
// response's own session_id (see submitMessage/handleAction).
export const currentSessionId = ref(null)
// Whether the session currently displayed accepts new messages — always
// true after the normal bootstrap/send/new-session flows (a session that
// was just touched is the active one by definition — see
// ChatSessionManager: at most one session is ever active per project, the
// most recently started *open* one, so "active" and "open" aren't the
// same thing), set to the backend's own `active` flag only when the user
// picks a session from the sessions panel (see selectSession) — never
// computed client-side. Starts false, not true: nothing has actually
// resolved a session yet at this point, and a project that's never been
// published can't create one at all (see db.create_chat_session) — the
// bootstrap in loadMessages()/ensureSession() then simply never gets to
// set this true, so ChatWindow.vue's own ActionButtons (gated on this,
// not recomputed from state.value, which the automaton alone can already
// populate with no session behind it at all) correctly stays out of the
// render path instead of showing manual-action buttons with nothing to
// fire them against.
export const selectedSessionActive = ref(false)
// Prompt 7 — GET /api/chat/session responds {paused: true, paused_reason}
// instead of a real session payload while the active project is paused
// (see ProjectService.recompute_availability); App.vue's own "Progetto
// in manutenzione" screen reads these instead of rendering chat. Reset
// to false at the top of every ensureSession() call, so switching to a
// project that's *not* paused clears it again without needing its own
// explicit reset anywhere else.
export const projectPaused = ref(false)
export const projectPausedReason = ref('')
export const sessions = ref([])
export const sessionsLoading = ref(false)
export const sessionsPanelOpen = ref(false)
// null in every context but one: EditProjectView.vue's own embedded
// "Test" chat sets this to its own projectName the instant 'test' mode
// becomes active (see its own watch(mode, ...)), and clears it back to
// null the instant it isn't — mode itself, and unmounting the view
// entirely, are the only two things that ever touch this, so it can
// never outlive the actual Test chat surface it describes. Read
// internally by every session bootstrap/list/refresh function below
// (ensureSession, loadSessions, toggleSessionsPanel, handleDeleteSession,
// ...) instead of threading a parameter through each one individually —
// several of those are reached from deep inside fully generic,
// mode-agnostic turn-processing code (handleSend/handleAction/...), where
// explicit threading would mean touching nearly every call in this file.
export const testModeProjectName = ref(null)
// The project the current session actually belongs to — set from the
// session payload's own project_name (see db.sessions._chat_session_to_
// dict) inside ensureSession() below, for both a live and a Test session
// alike. ChatWindow.vue's own index.css skin-loading fetch is the one
// caller: it's mounted for both App.vue's live chat and EditProjectView.
// vue's embedded Test chat, and unlike testModeProjectName (only ever set
// by the latter) this is the one place either can learn which project's
// files/index.css/content to actually fetch.
export const currentProjectName = ref(null)
export const messages = ref([])
export const historyLoaded = ref(false)
export const chatLoading = ref(false)
export const chatStatus = ref('')
export const actionLoading = ref(false)
export const autoTrackingEnabled = ref(true)
export const autoTrackingLoading = ref(false)

export const aiModels = ref([])
export const aiModelAuto = ref(true)
export const aiModelCurrentIndex = ref(0)
export const aiModelSelectionLoading = ref(false)
export const draft = ref('')

export const audioEnabled = ref(false)
export const talkAvailable = ref(true)
export const micAvailable = ref(true)
export const spokenTextEnabled = ref(false)

export const turnCount = ref(0)

let nextMessageId = 0

function bumpTurn() {
  turnCount.value++
}

export function setCapabilities({ talkAvailable: talk, micAvailable: mic }) {
  talkAvailable.value = talk
  micAvailable.value = mic
}

// `onEnter` is the *fired action's* own "on-enter" (see backend's
// automaton.Action.on_enter, sent over the wire as "on-enter" — see
// chat_service.py's apply_manual_action/_process_turn_locked) — not part
// of `newState` itself, since on-enter now describes how a state was
// entered, not the state itself. Callers with no actual transition to
// report (session load, boot ping) simply omit it — undefined never runs
// anything; every other caller (a real fired action, a brand new session
// entering through init-action, a reset) always passes its own, however
// it got it. Deliberately *not* gated on the state's own key having
// actually changed — a self-loop action (target === source, the shape
// every automaton.* trigger itself requires — see Prompt 6) still really
// fired and still deserves its own on-enter running, same as any other
// action; only whether the backend actually reported one at all decides
// this, never whether newState looks different from before. A free-form
// client-side script (see onEnterActions.js), not a fixed "celebrate"
// keyword: any expression calling into onEnterLocals, e.g. "celebrate()"
// or "notify('Nice!', 'You reached **state B**.')".
export function handleStateChange(newState, onEnter) {
  state.value = newState
  if (onEnter) {
    runOnEnterScript(onEnter)
  }
}

// Server-pushed cross-project wake-up (see backend's WakeupService/
// WsAdapter.push, wired through chatClient.js's own onNotification) —
// never tied to whichever turn is in flight, and can land for a project
// other than the one currently open. Only applies state.value/StateBar
// when the notification is actually about the project currently
// displayed (currentProjectName) — a foreign project's state would
// otherwise silently overwrite what the user is looking at. The
// on-enter script itself always runs regardless: its own locals
// (celebrate/notify, see onEnterActions.js) are project-agnostic UI, the
// same way a real turn's on-enter always runs whether or not this
// specific state looks new.
export function handleNotification({ project_name, state: newState, 'on-enter': onEnter }) {
  if (project_name === currentProjectName.value) {
    handleStateChange(newState, onEnter)
    return
  }
  if (onEnter) {
    runOnEnterScript(onEnter)
  }
}

onNotification(handleNotification)

// Shape every backend message row (id, role, content, audio_text,
// timestamp) into what the chat UI actually renders (see MessageBubble.
// vue/ChatTimeline.vue) — shared by every place that (re)loads a
// session's full history from scratch (loadMessages/selectSession/
// reloadMessages), so there's exactly one mapping to keep in sync with
// the backend's own row shape.
function toStoreMessage(m) {
  return { role: m.role, content: m.content, audioText: m.audio_text, timestamp: m.timestamp, failed: false, messageId: m.id }
}

// testModeProjectName (see its own docstring) set: EditProjectView.vue's
// own embedded "Test" chat, the one place a session is allowed to exist
// against a revision nobody's published yet — routed to a completely
// different pair of endpoints (getCurrentTestSession/postCreateTestSession
// below), never a flag on the shared ones (see api.js's own docstring on
// why). null: every other caller.
async function ensureSession() {
  projectPaused.value = false
  const session = testModeProjectName.value != null
    ? await getCurrentTestSession(currentSessionId.value, testModeProjectName.value)
    : await getCurrentSession(currentSessionId.value)
  if (session.paused) {
    projectPaused.value = true
    projectPausedReason.value = session.paused_reason || ''
    return null
  }
  currentSessionId.value = session.id
  selectedSessionActive.value = session.active
  currentProjectName.value = session.project_name
  return session.id
}

export async function loadMessages() {
  try {
    const sessionId = await ensureSession()
    if (sessionId == null) return  // paused — see ensureSession's own docstring
    const history = await getMessages(sessionId)
    messages.value = history.map(toStoreMessage)
    // Whichever project just became active, the sessions panel (if open)
    // was still showing the *previous* project's list (or the empty one
    // clearChatUi leaves it in) — without this, switching projects looks
    // like it wiped the sessions, when nothing server-side was touched.
    if (sessionsPanelOpen.value) await loadSessions()
  } catch {
    // already surfaced via apiFetch
  } finally {
    await nextTick()
    historyLoaded.value = true
  }
}

// testModeProjectName set (see its own docstring): EditProjectView.vue's
// own embedded "Test" chat's own sessions — includeImported is ignored
// there, since a "Test" session and an imported one are never the same
// list. testModeProjectName null: every other caller, unchanged
// (includeImported only ever true from LabelProjectView.vue).
// `projectName`, when given, is passed straight through to getSessions —
// LabelProjectView.vue's own caller always passes its own
// props.projectName explicitly (never relying on "whichever project is
// currently active" the way the main chat's own Sessions panel omitting
// it intentionally does), so reviewing project A's sessions never
// silently follows project B becoming active in the meantime (e.g. via
// uploading it). Ignored whenever testModeProjectName is set — that pool
// (EditProjectView.vue's own embedded "Test" chat) is already scoped by
// testModeProjectName instead, a separate mechanism of its own.
export async function loadSessions(includeImported = false, projectName = null) {
  sessionsLoading.value = true
  try {
    sessions.value = testModeProjectName.value != null
      ? await getTestSessions(testModeProjectName.value)
      : await getSessions(includeImported, projectName)
  } catch {
    // already surfaced via apiFetch
  } finally {
    sessionsLoading.value = false
  }
}

// Same fetch as loadSessions, but never touches sessionsLoading — for a
// caller that just wants `sessions` (e.g. its own has_annotations flags)
// brought current in the background, without flashing the shared Sessions
// panel (main page, EditProjectView, LabelProjectView all read the
// same sessionsLoading) to its "Loading…" placeholder over something the
// user never asked to reload.
export async function refreshSessionsQuietly(includeImported = false, projectName = null) {
  try {
    sessions.value = testModeProjectName.value != null
      ? await getTestSessions(testModeProjectName.value)
      : await getSessions(includeImported, projectName)
  } catch {
    // already surfaced via apiFetch
  }
}

export async function toggleSessionsPanel() {
  sessionsPanelOpen.value = !sessionsPanelOpen.value
  if (sessionsPanelOpen.value) {
    await loadSessions()
  }
}

// Switches the chat view to a specific past/present session, read directly
// (never through ensureSession/get_or_create_current_session — picking an
// old session must show *that* session's own history, not silently land
// on whichever one the backend considers "current"). `active` comes
// straight off the sessions-list entry the user clicked — the backend's
// own verdict, never recomputed here (a session can be individually
// "open" without being the active one — see ChatSessionManager).
export async function selectSession(session) {
  if (session.id === currentSessionId.value) return
  currentSessionId.value = session.id
  selectedSessionActive.value = session.active
  messages.value = []
  historyLoaded.value = false
  try {
    const history = await getMessages(session.id)
    messages.value = history.map(toStoreMessage)
  } catch {
    // already surfaced via apiFetch
  } finally {
    await nextTick()
    historyLoaded.value = true
  }
}

// Re-fetches the current session's own message history from scratch,
// in place — unlike selectSession, never a no-op for "already the
// current session" (that's exactly the case this exists for: the
// session itself hasn't changed, but what's *in* it just did — see
// handleTruncateFrom).
export async function reloadMessages() {
  if (currentSessionId.value == null) return
  try {
    messages.value = (await getMessages(currentSessionId.value)).map(toStoreMessage)
  } catch {
    // already surfaced via apiFetch
  }
}

// "Restart from here" (EditProjectView.vue's own chat only — see
// RestartFromHereButton.vue): deletes every message at/after `timestamp`
// in the current session, and rolls the live state back to match, then
// refreshes every piece of local state that depended on any of it.
// Callers decide what happens next with the cut-off message's own text
// (preload into the draft, or resend outright) — this only ever performs
// the truncation itself.
export async function handleTruncateFrom(timestamp) {
  if (currentSessionId.value == null) return
  try {
    const newState = await postTruncateSession(currentSessionId.value, timestamp)
    await reloadMessages()
    state.value = null
    handleStateChange(newState)
    bumpTurn()
  } catch {
    // already surfaced via apiFetch
  }
}

// Deletes a session and everything in it server-side (see
// db.delete_chat_session). If it was the one currently displayed, falls
// back to the same bootstrap loadMessages() uses on first load — there's
// no specific session left to keep showing.
export async function handleDeleteSession(session) {
  if (!window.confirm(`Delete this session (${session.end_state})? This cannot be undone.`)) return
  try {
    await deleteSession(session.id)
    if (session.id === currentSessionId.value) {
      currentSessionId.value = null
      await loadMessages()
    }
    await loadSessions()
  } catch {
    // already surfaced via apiFetch
  }
}

export async function loadAutoTracking() {
  try {
    const res = await getAutoTracking()
    autoTrackingEnabled.value = res.enabled
  } catch {
    // already surfaced via apiFetch
  }
}

export async function toggleAutoTracking() {
  autoTrackingLoading.value = true
  try {
    const res = await postAutoTracking(!autoTrackingEnabled.value)
    autoTrackingEnabled.value = res.enabled
  } catch {
    // already surfaced via apiFetch
  } finally {
    autoTrackingLoading.value = false
  }
}

// Applies the {auto, current_index, models} shape returned by both
// GET /api/ai/models and POST /api/ai/models/selection, and — piggybacked
// on every chat-turn/action response as `ai_model` (see chat_service.py) —
// keeps this in sync whenever a turn's own AI call causes the backend's
// cascade to fall back to a different model, with no extra round trip.
function applyAiModelInfo(info) {
  aiModels.value = info.models
  aiModelAuto.value = info.auto
  aiModelCurrentIndex.value = info.current_index
}

export async function loadAiModels() {
  try {
    applyAiModelInfo(await getAiModels())
  } catch {
    // already surfaced via apiFetch
  }
}

export async function selectAiModel(index) {
  aiModelSelectionLoading.value = true
  try {
    applyAiModelInfo(await postAiModelSelection(index))
  } catch {
    // already surfaced via apiFetch
  } finally {
    aiModelSelectionLoading.value = false
  }
}

export function toggleAudio() {
  audioEnabled.value = !audioEnabled.value
  if (audioEnabled.value) {
    const lastAssistant = [...messages.value].reverse().find((m) => m.role === 'assistant' && m.messageId != null)
    if (lastAssistant) playMessageAudio(messageAudioUrl(lastAssistant.messageId))
  }
}

export function toggleSpokenText() {
  spokenTextEnabled.value = !spokenTextEnabled.value
}

function maybeAutoPlayAudio(messageId) {
  if (!audioEnabled.value || messageId == null) return
  playMessageAudio(messageAudioUrl(messageId))
}

function setMessageFailed(id, failed) {
  const target = messages.value.find((m) => m.id === id)
  if (target) target.failed = failed
}

async function submitMessage(message) {
  clearApiError()
  setMessageFailed(message.id, false)
  chatLoading.value = true

  // Creiamo subito la bolla dell'assistente che accoglierà i chunk in tempo reale
  const assistantMsgId = ++nextMessageId
  const assistantMsg = {
    id: assistantMsgId,
    role: 'assistant',
    content: '',
    audioText: null,
    messageId: null,
    timestamp: new Date().toISOString()
  }
  messages.value.push(assistantMsg)

  try {
    // Passiamo le callback onStatus e onChunk a sendChatMessage
    const result = await sendChatMessage(message.content, currentSessionId.value, {
      onStatus: (text) => {
        chatStatus.value = text
      },
      onChunk: (chunkText) => {
        // Troviamo l'indice del messaggio e aggiorniamo il valore creando un nuovo oggetto per scatenare la reattività di Vue
        const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx] = {
            ...messages.value[idx],
            content: messages.value[idx].content + chunkText
          }
        }
      }
    })

    // Correla questa bolla con il suo vero id lato backend — serve a
    // ChatTimeline/benchmarkTimeline.js's effectiveTimestamp per
    // posizionare una transizione pre-turno (autotracking_on_user_
    // message) esattamente su questo messaggio invece di ricadere sul
    // timestamp grezzo lato server, confrontato — a torto — con
    // l'orologio client della bolla assistant (vedi EditProjectView.vue's
    // rawLiveMessages). Usa direttamente assistant_message_id/
    // user_message_id — non result.reply, che chat_service.py's
    // process_turn non popola mai (OutVariables.messages resta sempre
    // [], vedi backend tests/test_chat_service_evaluation_points.py) —
    // prima si leggeva da lì e la bolla non veniva mai correlata al suo
    // vero id, perdendo la linea di separazione/i segnali per ogni turno.
    if (result.user_message_id != null) message.messageId = result.user_message_id

    const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
    if (idx !== -1) {
      if (result.assistant_message_id != null) {
        // Anche il timestamp va ristampato qui, non solo messageId: quello
        // originale risale a quando questa bolla placeholder è stata
        // creata (submitMessage's own push, sostanzialmente lo stesso
        // istante del messaggio utente che l'ha innescata), mai aggiornato
        // con quanto la risposta ha davvero impiegato — così il prossimo
        // turno, se inviato in fretta, può ricevere un timestamp locale
        // vicinissimo (o persino coincidente) a quello di questa bolla.
        // Un pareggio timestamp fa sì che buildTimeline's own tie-break
        // (un messaggio precede sempre una transizione allo stesso istante
        // effettivo) spinga la transizione oltre bolle successive che in
        // realtà dovrebbe precedere.
        messages.value[idx] = {
          ...messages.value[idx],
          messageId: result.assistant_message_id,
          timestamp: new Date().toISOString()
        }
      } else {
        // Nessuna risposta AI generata questo turno (es. una transizione
        // pre-turno è atterrata in uno stato che non chatta affatto) —
        // niente è mai stato trasmesso in questa bolla, quindi la
        // rimuoviamo invece di lasciarla vuota e orfana.
        messages.value.splice(idx, 1)
      }
    }

    playMessageChime()

    if (result.assistant_message_id != null) {
      maybeAutoPlayAudio(result.assistant_message_id)
    }

    if (result.state) {
      handleStateChange(result.state, result['on-enter'])
    }
    if (result.ai_model) {
      applyAiModelInfo(result.ai_model)
    }
    if (result.session_id != null) {
      // A turn always lands on a session it just touched (see
      // ChatSessionManager) — open by definition.
      currentSessionId.value = result.session_id
      selectedSessionActive.value = true
    }
    if (sessionsPanelOpen.value) loadSessions()
    bumpTurn()
  } catch (err) {
    // In caso di errore durante l'invio, rimuoviamo la bolla vuota/incompleta
    const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
    if (idx !== -1) messages.value.splice(idx, 1)

    setMessageFailed(message.id, true)
    // 409 = the backend rejected this exact session_id as closed (see
    // ChatSessionManager.require_open_session) — reflect that immediately
    // so the input disables and action buttons hide without a reload.
    if (err.status === 409) selectedSessionActive.value = false
  } finally {
    chatLoading.value = false
    chatStatus.value = ''
  }
}

export async function handleSend(text) {
  const message = { id: ++nextMessageId, role: 'user', content: text, failed: false, timestamp: new Date().toISOString() }
  messages.value.push(message)
  await submitMessage(message)
}

function dropVoicePlaceholder(id) {
  const idx = messages.value.findIndex((m) => m.id === id)
  if (idx !== -1) messages.value.splice(idx, 1)
}

export async function handleVoiceMessage(audioBlob) {
  const message = { id: ++nextMessageId, role: 'user', content: '', failed: false, transcribing: true }
  messages.value.push(message)

  let text
  try {
    const result = await postListenTranscribe(audioBlob)
    text = result.text?.trim()
  } catch {
    dropVoicePlaceholder(message.id)
    return
  }
  if (!text) {
    dropVoicePlaceholder(message.id)
    return
  }

  message.content = text
  message.transcribing = false
  await submitMessage(message)
}

export async function handleResend(index) {
  if (chatLoading.value) return
  const message = messages.value[index]
  if (!message || message.role !== 'user') return
  await submitMessage(message)
}

export async function handleAction(actionName) {
  actionLoading.value = true
  try {
    const result = await postAction(actionName, currentSessionId.value)
    for (const { id, content, audio_text, timestamp } of result.reply) {
      messages.value.push({
        role: 'assistant',
        content,
        audioText: audio_text,
        messageId: id,
        // The backend's own real timestamp (see ChatService.
        // _messages_for_transition/db.get_message) — not the client's
        // clock: two entries can land here from the very same action
        // (an action_prompt reply plus a separate opening message), and
        // stamping both with "now" risks the exact same tie/near-tie
        // buildTimeline's own tie-break mishandled for submitMessage's
        // assistant bubble (see its own comment there).
        timestamp
      })
    }
    if (result.reply.length) {
      playMessageChime()
      maybeAutoPlayAudio(result.reply[result.reply.length - 1].id)
    }
    handleStateChange(result.state, result['on-enter'])
    if (result.ai_model) {
      applyAiModelInfo(result.ai_model)
    }
    if (result.session_id != null) {
      currentSessionId.value = result.session_id
      selectedSessionActive.value = true
    }
    if (sessionsPanelOpen.value) loadSessions()
    bumpTurn()
  } catch (err) {
    // already surfaced via apiFetch
    if (err.status === 409) selectedSessionActive.value = false
  } finally {
    actionLoading.value = false
  }
}

export function clearChatUi() {
  messages.value = []
  clearApiError()
  chatStatus.value = ''
  autoTrackingEnabled.value = true
  // reset_project/reset_all wipe ChatSession rows too (see db.py) — a
  // stale id here would just be ignored server-side, but a project
  // switch is exactly when "the current session" should be re-resolved.
  currentSessionId.value = null
  currentProjectName.value = null
  selectedSessionActive.value = true
  sessions.value = []
}

// testModeProjectName (see its own docstring) is read internally by
// loadMessages/ensureSession — still works from EditProjectView.vue's own
// embedded "Test" chat toolbar for a project that's never been published.
export async function handleReset() {
  if (!window.confirm('Reset the conversation, signals, and transitions? This cannot be undone.')) return
  clearChatUi()
  try {
    // A reset re-enters the automaton through init-action the same way a
    // session's very first transition ever does (see controller.py's own
    // post_reset docstring) — its own on-enter rides along on this same
    // response, under the same "on-enter" wire key as every other real
    // transition.
    const { 'on-enter': onEnter, ...newState } = await postReset()
    state.value = null
    handleStateChange(newState, onEnter)
    await loadMessages()
    bumpTurn()
  } catch {
    // already surfaced via apiFetch
  }
}

// testModeProjectName (see its own docstring), read internally — its own
// SessionsPanel "new session" button is what reaches this, from either
// context alike.
export async function handleNewSession() {
  // Only one session is ever active per project (see ChatSessionManager) —
  // starting a new one always supersedes whichever one was current, so
  // this is a real "close the current session" action, not just an addition.
  if (!window.confirm('Start a new session? This will close the current session for this project — only one can be active at a time.')) return
  try {
    const session = testModeProjectName.value != null
      ? await postCreateTestSession(testModeProjectName.value)
      : await postCreateSession()
    currentSessionId.value = session.id
    selectedSessionActive.value = session.active
    clearApiError()
    messages.value = []
    // A brand new session always starts by entering init_action.target
    // *through* init_action itself (see ChatService.create_session's own
    // docstring) — same "on-enter" wire key every other real transition
    // reports.
    if (session['on-enter']) runOnEnterScript(session['on-enter'])
    await loadMessages()
    // Opened unconditionally (not just refreshed when already open) so the
    // new session is actually visible right away, wherever this was
    // triggered from — not dependent on the sessions panel already being open.
    sessionsPanelOpen.value = true
    await loadSessions()
    bumpTurn()
  } catch {
    // already surfaced via apiFetch
  }
}