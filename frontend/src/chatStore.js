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
// The chat conversation's current session_id — null until the first
// loadMessages()/ensureSession() bootstrap. Every write call must carry
// it; kept in sync from each response's own session_id.
export const currentSessionId = ref(null)
// Whether the session currently displayed accepts new messages. Set to
// the backend's own `active` flag only when the user picks a session
// (see selectSession) — never computed client-side.
export const selectedSessionActive = ref(false)
// GET /api/chat/session responds {paused: true, paused_reason} instead
// of a real session payload while the active project is paused; App.vue's
// maintenance screen reads these instead of rendering chat.
export const projectPaused = ref(false)
export const projectPausedReason = ref('')
export const sessions = ref([])
export const sessionsLoading = ref(false)
export const sessionsPanelOpen = ref(false)
// null except for EditProjectView's embedded "Test" chat, which sets
// this to its own projectName while 'test' mode is active. Read
// internally instead of threading a parameter through each call site.
export const testModeProjectName = ref(null)
// The project the current session actually belongs to, set from the
// session payload inside ensureSession() below. ChatWindow's index.css
// skin-loading fetch uses this to know which project's files to fetch.
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

// `onEnter` is the fired action's own "on-enter" script — not part of
// `newState` itself. Callers with nothing to report simply omit it.
// Deliberately not gated on the state key having changed — a self-loop still fires its own on-enter.
export function handleStateChange(newState, onEnter) {
  state.value = newState
  if (onEnter) {
    runOnEnterScript(onEnter)
  }
}

// A server-pushed cross-project wake-up — can land for a project other
// than the one currently open. Only applies state.value/StateBar when
// the notification is about the currently displayed project.
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

// Shapes a backend message row into what the chat UI renders — shared by
// every place that (re)loads a session's full history from scratch.
function toStoreMessage(m) {
  return { role: m.role, content: m.content, audioText: m.audio_text, timestamp: m.timestamp, failed: false, messageId: m.id }
}

// testModeProjectName set: EditProjectView's embedded "Test" chat,
// routed to a separate pair of endpoints. null: every other caller.
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
  if (testModeProjectName.value != null) await loadAutoTracking()
  return session.id
}

export async function loadMessages() {
  try {
    const sessionId = await ensureSession()
    if (sessionId == null) return  // paused
    const history = await getMessages(sessionId)
    messages.value = history.map(toStoreMessage)
    // The sessions panel (if open) was still showing the previous
    // project's list — refresh it so switching projects doesn't look
    // like it wiped the sessions.
    if (sessionsPanelOpen.value) await loadSessions()
  } catch {
    // already surfaced via apiFetch
  } finally {
    await nextTick()
    historyLoaded.value = true
  }
}

// testModeProjectName set: EditProjectView's embedded "Test" chat's own
// sessions, a separate pool. `projectName` omitted falls back to
// currentProjectName; Label/Auto views always pass their own explicitly.
export async function loadSessions(includeImported = false, projectName = null) {
  sessionsLoading.value = true
  try {
    sessions.value = testModeProjectName.value != null
      ? await getTestSessions(testModeProjectName.value)
      : await getSessions(projectName ?? currentProjectName.value, includeImported)
  } catch {
    // already surfaced via apiFetch
  } finally {
    sessionsLoading.value = false
  }
}

// Same fetch as loadSessions, but never touches sessionsLoading — for a
// caller that wants `sessions` refreshed without flashing the panel to
// "Loading…".
export async function refreshSessionsQuietly(includeImported = false, projectName = null) {
  try {
    sessions.value = testModeProjectName.value != null
      ? await getTestSessions(testModeProjectName.value)
      : await getSessions(projectName ?? currentProjectName.value, includeImported)
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

// Switches the chat view to a specific past/present session, read
// directly (never through ensureSession, which would land on the
// "current" session instead of the one picked). `active` is never recomputed.
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

// Re-fetches the current session's message history from scratch, in
// place — unlike selectSession, never a no-op for "already the current
// session" (the session hasn't changed, but what's in it just did).
export async function reloadMessages() {
  if (currentSessionId.value == null) return
  try {
    messages.value = (await getMessages(currentSessionId.value)).map(toStoreMessage)
  } catch {
    // already surfaced via apiFetch
  }
}

// "Restart from here" (EditProjectView's chat only): deletes every
// message at/after `timestamp` and rolls state back to match. Callers
// decide what happens with the cut-off text — this only truncates.
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

// Deletes a session and everything in it server-side. If it was the one
// currently displayed, falls back to the same bootstrap loadMessages()
// uses on first load.
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

// Test-session-only — called from ensureSession() below, gated on
// testModeProjectName. currentSessionId is always the test session by
// the time this runs.
async function loadAutoTracking() {
  try {
    const res = await getAutoTracking(currentSessionId.value)
    autoTrackingEnabled.value = res.enabled
  } catch {
    // already surfaced via apiFetch
  }
}

export async function toggleAutoTracking() {
  autoTrackingLoading.value = true
  try {
    const res = await postAutoTracking(currentSessionId.value, !autoTrackingEnabled.value)
    autoTrackingEnabled.value = res.enabled
  } catch {
    // already surfaced via apiFetch
  } finally {
    autoTrackingLoading.value = false
  }
}

// Applies the {auto, current_index, models} shape returned by both
// GET /api/ai/models and POST /api/ai/models/selection, and piggybacked
// on every chat-turn response as `ai_model` when a turn falls back to a different model.
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

  // Create the assistant bubble up front, to receive chunks as they stream in
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
    const result = await sendChatMessage(message.content, currentSessionId.value, {
      onStatus: (text) => {
        chatStatus.value = text
      },
      onChunk: (chunkText) => {
        // Replace with a new object (not mutate in place) to trigger Vue reactivity
        const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
        if (idx !== -1) {
          messages.value[idx] = {
            ...messages.value[idx],
            content: messages.value[idx].content + chunkText
          }
        }
      }
    })

    // Correlate this bubble with its real backend id — needed by
    // benchmarkTimeline.js's effectiveTimestamp to position a pre-turn
    // transition exactly on this message rather than a raw server
    // timestamp. Read directly from assistant_message_id/user_message_id,
    // never from result.reply (always empty for a live turn).
    if (result.user_message_id != null) message.messageId = result.user_message_id

    const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
    if (idx !== -1) {
      if (result.assistant_message_id != null) {
        // The timestamp must be re-stamped too, not just messageId — the
        // placeholder's original timestamp predates however long the
        // response actually took, which can tie (or nearly tie) with the
        // next turn's own timestamp and confuse buildTimeline's tie-break.
        messages.value[idx] = {
          ...messages.value[idx],
          messageId: result.assistant_message_id,
          timestamp: new Date().toISOString()
        }
      } else {
        // No AI reply was generated this turn (e.g. a pre-turn transition
        // landed in a state that doesn't chat at all) — remove the empty,
        // orphaned bubble instead of leaving it.
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
      // A turn always lands on a session it just touched — open by definition.
      currentSessionId.value = result.session_id
      selectedSessionActive.value = true
    }
    if (sessionsPanelOpen.value) loadSessions()
    bumpTurn()
  } catch (err) {
    // On send failure, remove the empty/incomplete bubble
    const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
    if (idx !== -1) messages.value.splice(idx, 1)

    setMessageFailed(message.id, true)
    // 409 = the backend rejected this session_id as closed — reflect that
    // immediately so the input disables without a reload.
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
        // The backend's real timestamp, not the client's clock — two
        // entries can land here from the same action, and stamping both
        // with "now" risks the same tie buildTimeline mishandles.
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
  // A project switch is exactly when "the current session" should be re-resolved.
  currentSessionId.value = null
  currentProjectName.value = null
  selectedSessionActive.value = true
  sessions.value = []
}

// testModeProjectName is read internally by loadMessages/ensureSession —
// still works from EditProjectView's embedded "Test" chat toolbar for a
// project that's never been published.
export async function handleReset() {
  if (!window.confirm('Reset the conversation, signals, and transitions? This cannot be undone.')) return
  clearChatUi()
  try {
    // A reset re-enters the automaton through init-action, same as a
    // session's very first transition — its on-enter rides along under
    // the same "on-enter" wire key as any other real transition.
    const { 'on-enter': onEnter, ...newState } = await postReset()
    state.value = null
    handleStateChange(newState, onEnter)
    await loadMessages()
    bumpTurn()
  } catch {
    // already surfaced via apiFetch
  }
}

// testModeProjectName, read internally — the SessionsPanel "new session"
// button reaches this from either context alike.
export async function handleNewSession() {
  // Only one session is ever active per project — starting a new one
  // always supersedes the current one, not just adds to it.
  if (!window.confirm('Start a new session? This will close the current session for this project — only one can be active at a time.')) return
  try {
    const session = testModeProjectName.value != null
      ? await postCreateTestSession(testModeProjectName.value)
      : await postCreateSession()
    currentSessionId.value = session.id
    selectedSessionActive.value = session.active
    clearApiError()
    messages.value = []
    // A brand new session enters init_action.target through init_action
    // itself, reported under the same "on-enter" wire key as any other
    // real transition.
    if (session['on-enter']) runOnEnterScript(session['on-enter'])
    await loadMessages()
    // Opened unconditionally so the new session is visible right away,
    // regardless of whether the panel was already open.
    sessionsPanelOpen.value = true
    await loadSessions()
    bumpTurn()
  } catch {
    // already surfaced via apiFetch
  }
}