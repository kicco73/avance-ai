import { nextTick, ref } from 'vue'
import {
  getMessages, getSessionState, postAction, getAutoTracking, postAutoTracking,
  postTruncateSession, deleteSession, putMessageReaction, postListenTranscribe, messageAudioUrl,
  getAiModels, postAiModelSelection
} from './api.js'
import { sendMessage as sendChatMessage, onNotification } from './chatClient.js'
import { playMessageChime, playMessageAudio, playReactionChime } from './audio.js'
import { runOnEnterScript } from './onEnterActions.js'
import { clearApiError } from './errorStore.js'
import { confirmDialog } from './dialogStore.js'
import { registerSkinSource } from './chatSkin.js'

// App-wide user preferences — genuinely not "which chat" state, so a
// single shared instance regardless of how many chat stores exist (see
// createChatStore below).
export const audioEnabled = ref(false)
export const talkAvailable = ref(true)
export const micAvailable = ref(true)
export const spokenTextEnabled = ref(false)

export const aiModels = ref([])
export const aiModelAuto = ref(true)
export const aiModelCurrentIndex = ref(0)
export const aiModelSelectionLoading = ref(false)

export function setCapabilities({ talkAvailable: talk, micAvailable: mic }) {
  talkAvailable.value = talk
  micAvailable.value = mic
}

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

export function toggleSpokenText() {
  spokenTextEnabled.value = !spokenTextEnabled.value
}

// One independent chat conversation's worth of state — the live chat and
// EditProjectView's embedded "Run" test chat each get their own instance,
// never sharing a session id/messages/automaton state with the other.
// `kind` ('live'|'test') only ever drives chatSkin.js's routing, nothing
// about session resolution itself.
export function createChatStore({
  kind, getCurrentSession, getSessionsList, createSession, resetSession = null,
  confirmNewSession = true, useAutoTracking = false, subscribeToNotifications = false,
}) {
  const state = ref(null)
  const currentSessionId = ref(null)
  const selectedSessionActive = ref(false)
  const projectPaused = ref(false)
  const projectPausedReason = ref('')
  const sessions = ref([])
  const sessionsLoading = ref(false)
  const sessionsPanelOpen = ref(false)
  const currentProjectName = ref(null)
  const messages = ref([])
  const historyLoaded = ref(false)
  const chatLoading = ref(false)
  const chatStatus = ref('')
  const actionLoading = ref(false)
  const autoTrackingEnabled = ref(true)
  const autoTrackingLoading = ref(false)
  const draft = ref('')
  const turnCount = ref(0)
  let nextMessageId = 0
  // Set (never auto-cleared — see ensureSession/handleNewSession below)
  // whenever a freshly-created live session's own legal/terms.md changed
  // since this user's previous one here; only acceptLegalTerms clears it.
  // Stays false forever for the 'test' store, since the backend only ever
  // reports it for a 'live' session.
  const legalTermsPending = ref(false)

  function acceptLegalTerms() {
    legalTermsPending.value = false
  }

  registerSkinSource(kind, currentProjectName, currentSessionId)

  function bumpTurn() {
    turnCount.value++
  }

  // `onEnter` is the fired action's own "on-enter" script — not part of
  // `newState` itself. Callers with nothing to report simply omit it.
  // Deliberately not gated on the state key having changed — a self-loop
  // still fires its own on-enter.
  function handleStateChange(newState, onEnter) {
    state.value = newState
    if (onEnter) {
      runOnEnterScript(onEnter)
    }
  }

  if (subscribeToNotifications) {
    // A server-pushed cross-project wake-up — can land for a project
    // other than the one currently open. Only applies state.value when
    // the notification is about the currently displayed project.
    onNotification(({ project_name, state: newState, 'on-enter': onEnter }) => {
      if (project_name === currentProjectName.value) {
        handleStateChange(newState, onEnter)
        return
      }
      if (onEnter) runOnEnterScript(onEnter)
    })
  }

  // Shapes a backend message row into what the chat UI renders — shared
  // by every place that (re)loads a session's full history from scratch.
  function toStoreMessage(m) {
    return {
      role: m.role, content: m.content, audioText: m.audio_text, reaction: m.reaction,
      timestamp: m.timestamp, failed: false, messageId: m.id
    }
  }

  async function loadAutoTracking() {
    try {
      const res = await getAutoTracking(currentSessionId.value)
      autoTrackingEnabled.value = res.enabled
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function ensureSession() {
    projectPaused.value = false
    const session = await getCurrentSession(currentSessionId.value)
    if (session.paused) {
      projectPaused.value = true
      projectPausedReason.value = session.paused_reason || ''
      return null
    }
    currentSessionId.value = session.id
    selectedSessionActive.value = session.active
    currentProjectName.value = session.project_name
    state.value = session.state
    // Only ever set true here, never false — `legal_terms_pending` is
    // only present in the response at all on the one bootstrap call that
    // actually created this session; a later resolve of the same session
    // (e.g. a page reload before Accept) omits it, and overwriting the
    // ref to false in that case would silently dismiss the gate without
    // acceptLegalTerms() ever having been called.
    if (session.legal_terms_pending) legalTermsPending.value = true
    if (useAutoTracking) await loadAutoTracking()
    return session.id
  }

  async function loadMessages() {
    try {
      const sessionId = await ensureSession()
      if (sessionId == null) return // paused
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

  async function loadSessions(includeImported = false, projectName = null) {
    sessionsLoading.value = true
    try {
      sessions.value = await getSessionsList(includeImported, projectName ?? currentProjectName.value)
    } catch {
      // already surfaced via apiFetch
    } finally {
      sessionsLoading.value = false
    }
  }

  // Same fetch as loadSessions, but never touches sessionsLoading — for a
  // caller that wants `sessions` refreshed without flashing the panel to
  // "Loading…".
  async function refreshSessionsQuietly(includeImported = false, projectName = null) {
    try {
      sessions.value = await getSessionsList(includeImported, projectName ?? currentProjectName.value)
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function toggleSessionsPanel() {
    sessionsPanelOpen.value = !sessionsPanelOpen.value
    if (sessionsPanelOpen.value) {
      await loadSessions()
    }
  }

  // Switches the chat view to a specific past/present session, read
  // directly (never through ensureSession, which would land on the
  // "current" session instead of the one picked). `active` is never
  // recomputed.
  async function selectSession(session) {
    if (session.id === currentSessionId.value) return
    currentSessionId.value = session.id
    selectedSessionActive.value = session.active
    messages.value = []
    historyLoaded.value = false
    try {
      const [history, sessionState] = await Promise.all([getMessages(session.id), getSessionState(session.id)])
      messages.value = history.map(toStoreMessage)
      state.value = sessionState
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
  async function reloadMessages() {
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
  async function handleTruncateFrom(timestamp) {
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

  // Deletes a session and everything in it server-side. If it was the
  // one currently displayed, falls back to the same bootstrap
  // loadMessages() uses on first load.
  async function handleDeleteSession(session) {
    const ok = await confirmDialog({
      title: 'Delete session',
      body: `Delete this session (${session.end_state})? This cannot be undone.`,
      okLabel: 'Delete',
      danger: true
    })
    if (!ok) return
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

  async function toggleAutoTracking() {
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

  function toggleAudio() {
    audioEnabled.value = !audioEnabled.value
    if (audioEnabled.value) {
      const lastAssistant = [...messages.value].reverse().find((m) => m.role === 'assistant' && m.messageId != null)
      if (lastAssistant) playMessageAudio(messageAudioUrl(lastAssistant.messageId))
    }
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
      // Same "replace, don't mutate in place" rule as onChunk above — `message`
      // is the raw object this closure was handed, not the reactive proxy
      // Vue wraps around whatever's actually sitting in messages.value, so
      // mutating it directly here would silently never re-render.
      const userIdx = messages.value.findIndex((m) => m.id === message.id)
      if (userIdx !== -1) {
        messages.value[userIdx] = {
          ...messages.value[userIdx],
          messageId: result.user_message_id ?? messages.value[userIdx].messageId,
          // The bot's own reaction to this user message, if any — applied
          // live here so the UI doesn't need a full messages refetch to show
          // it (see TrackingProcessor._build_turn_response's own user_message_reaction).
          reaction: result.user_message_reaction ?? null
        }
        if (result.user_message_reaction) playReactionChime()
      }

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

  async function handleSend(text) {
    const message = { id: ++nextMessageId, role: 'user', content: text, failed: false, timestamp: new Date().toISOString() }
    messages.value.push(message)
    await submitMessage(message)
  }

  function dropVoicePlaceholder(id) {
    const idx = messages.value.findIndex((m) => m.id === id)
    if (idx !== -1) messages.value.splice(idx, 1)
  }

  async function handleVoiceMessage(audioBlob) {
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

  async function handleResend(index) {
    if (chatLoading.value) return
    const message = messages.value[index]
    if (!message || message.role !== 'user') return
    await submitMessage(message)
  }

  // The user's own reaction to a bot message — keyed by messageId, not
  // position, so both ChatWindow.vue's own default timeline and
  // ChatTimeline.vue's message+transition one (RunChat.vue/LabelProjectView.vue)
  // can call this the same way despite indexing messages differently.
  async function handleReact(messageId, reaction) {
    const message = messages.value.find((m) => m.messageId === messageId)
    if (!message || message.role !== 'assistant') return
    try {
      await putMessageReaction(messageId, reaction)
      message.reaction = reaction
      if (reaction) playReactionChime()
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleAction(actionName) {
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

  function clearChatUi() {
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

  async function handleReset() {
    const ok = await confirmDialog({
      title: 'Reset conversation',
      body: 'Reset the conversation, signals, and transitions? This cannot be undone.',
      okLabel: 'Reset',
      danger: true
    })
    if (!ok) return
    clearChatUi()
    try {
      // A reset re-enters the automaton through init-action, same as a
      // session's very first transition — its on-enter rides along under
      // the same "on-enter" wire key as any other real transition.
      const { 'on-enter': onEnter, ...newState } = await resetSession()
      state.value = null
      handleStateChange(newState, onEnter)
      await loadMessages()
      bumpTurn()
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleNewSession() {
    // Only one session is ever active per project — starting a new one
    // always supersedes the current one, not just adds to it. Test mode's
    // draft sessions are cheap and disposable, so this confirmation only
    // guards the real/live session pool.
    if (confirmNewSession) {
      const ok = await confirmDialog({
        title: 'Start new session',
        body: 'Start a new session? This will close the current session — only one can be active at a time.',
        okLabel: 'Start'
      })
      if (!ok) return
    }
    try {
      const session = await createSession()
      currentSessionId.value = session.id
      selectedSessionActive.value = session.active
      // Same one-shot signal as ensureSession's own — this call always
      // creates a genuinely new session, so unlike there, no "only ever
      // set true" guard is needed: there's nothing stale to protect against.
      if (session.legal_terms_pending) legalTermsPending.value = true
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

  return {
    state, currentSessionId, selectedSessionActive, projectPaused, projectPausedReason,
    sessions, sessionsLoading, sessionsPanelOpen, currentProjectName,
    messages, historyLoaded, chatLoading, chatStatus, actionLoading,
    autoTrackingEnabled, autoTrackingLoading, draft, turnCount,
    legalTermsPending, acceptLegalTerms,
    handleStateChange, loadMessages, loadSessions, refreshSessionsQuietly, toggleSessionsPanel,
    selectSession, reloadMessages, handleTruncateFrom, handleDeleteSession, toggleAutoTracking,
    toggleAudio, handleSend, handleVoiceMessage, handleResend, handleReact, handleAction,
    clearChatUi, handleReset: resetSession ? handleReset : null, handleNewSession,
  }
}
