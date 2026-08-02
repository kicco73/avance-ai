import { nextTick, ref } from 'vue'
import {
  getCurrentSession,
  postCreateSession,
  getSessions,
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
import { sendMessage as sendChatMessage } from './chatClient.js'
import { playMessageChime, playMessageAudio } from './audio.js'
import { celebrate } from './confetti.js'
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
// computed client-side.
export const selectedSessionActive = ref(true)
export const sessions = ref([])
export const sessionsLoading = ref(false)
export const sessionsPanelOpen = ref(false)
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
// report (session load, boot ping, reset, restart-from-here) simply omit
// it — undefined never celebrates.
export function handleStateChange(newState, onEnter) {
  const changed = state.value?.key !== newState?.key
  state.value = newState
  if (changed && onEnter === 'celebrate') {
    celebrate()
  }
}

// Shape every backend message row (id, role, content, audio_text,
// timestamp) into what the chat UI actually renders (see MessageBubble.
// vue/ChatTimeline.vue) — shared by every place that (re)loads a
// session's full history from scratch (loadMessages/selectSession/
// reloadMessages), so there's exactly one mapping to keep in sync with
// the backend's own row shape.
function toStoreMessage(m) {
  return { role: m.role, content: m.content, audioText: m.audio_text, timestamp: m.timestamp, failed: false, messageId: m.id }
}

async function ensureSession() {
  const session = await getCurrentSession(currentSessionId.value)
  currentSessionId.value = session.id
  selectedSessionActive.value = session.active
  return session.id
}

export async function loadMessages() {
  try {
    const sessionId = await ensureSession()
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

export async function loadSessions() {
  sessionsLoading.value = true
  try {
    sessions.value = await getSessions()
  } catch {
    // already surfaced via apiFetch
  } finally {
    sessionsLoading.value = false
  }
}

// Same fetch as loadSessions, but never touches sessionsLoading — for a
// caller that just wants `sessions` (e.g. its own has_annotations flags)
// brought current in the background, without flashing the shared Sessions
// panel (main page, EditProjectView, BenchmarkProjectView all read the
// same sessionsLoading) to its "Loading…" placeholder over something the
// user never asked to reload.
export async function refreshSessionsQuietly() {
  try {
    sessions.value = await getSessions()
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

    // Alla ricezione del frame 'done':
    if (result.reply && result.reply.length > 0) {
      const firstReply = result.reply[0]
      const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
      
      if (idx !== -1) {
        // Assegniamo l'ID definitivo e l'audio_text alla bolla che ha raccolto lo streaming
        messages.value[idx] = {
          ...messages.value[idx],
          messageId: firstReply.id,
          audioText: firstReply.audio_text,
          // Sincronizziamo con il contenuto restituito dal server se fornito
          content: firstReply.content || messages.value[idx].content
        }
      }

      // Se la risposta contiene più messaggi (es. cambio di stato)
      for (let i = 1; i < result.reply.length; i++) {
        const { id, content, audio_text } = result.reply[i]
        messages.value.push({
          role: 'assistant',
          content,
          audioText: audio_text,
          messageId: id,
          timestamp: new Date().toISOString()
        })
      }
    }

    playMessageChime()

    if (result.reply && result.reply.length > 0) {
      maybeAutoPlayAudio(result.reply[result.reply.length - 1].id)
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
    for (const { id, content, audio_text } of result.reply) {
      messages.value.push({
        role: 'assistant',
        content,
        audioText: audio_text,
        messageId: id,
        timestamp: new Date().toISOString()
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
  selectedSessionActive.value = true
  sessions.value = []
}

export async function handleReset() {
  if (!window.confirm('Reset the conversation, signals, and transitions? This cannot be undone.')) return
  clearChatUi()
  try {
    const newState = await postReset()
    state.value = null
    handleStateChange(newState)
    await loadMessages()
    bumpTurn()
  } catch {
    // already surfaced via apiFetch
  }
}

export async function handleNewSession() {
  // Only one session is ever active per project (see ChatSessionManager) —
  // starting a new one always supersedes whichever one was current, so
  // this is a real "close the current session" action, not just an addition.
  if (!window.confirm('Start a new session? This will close the current session for this project — only one can be active at a time.')) return
  try {
    const session = await postCreateSession()
    currentSessionId.value = session.id
    selectedSessionActive.value = session.active
    clearApiError()
    messages.value = []
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