import { computed, nextTick, ref } from 'vue'
import {
  getMessages, getSessionState, postAction, getAutoTracking, postAutoTracking, getActuators, postActuators,
  postTruncateSession, deleteSession, postCloseSession, putMessageReaction, postListenTranscribe, messageAudioUrl,
} from './api.js'
import { sendMessage as sendChatMessage, onConnectionState, getConnectionState } from './chatClient.js'
import { ChatReconnectSync } from './chatReconnectSync.js'
import { applyAiModelInfo } from './aiModelStore.js'
import { ToolStatusHold } from './toolStatusHold.js'
import { subscribeToStateNotifications } from './notificationBus.js'
import { playMessageChime, playMessageAudio, playReactionChime, unlockAudioPlayback } from './audio.js'
import { clearApiError, setApiError } from './errorStore.js'
import { confirmDialog } from './dialogStore.js'
import { registerSkinSource } from './chatSkin.js'

const SESSION_INACTIVE_CODES = ['session_closed', 'session_channel_mismatch', 'session_superseded']

// The chat's one transport, as the UI sees it: 'connecting' | 'open' |
// 'closed' (see chatClient.js). Module-level, not per-store — there is
// exactly one socket per page, whichever chats are open on it.
export const chatConnectionState = ref(getConnectionState())
onConnectionState((next) => { chatConnectionState.value = next })

// App-wide user preferences — genuinely not "which chat" state, so a
// single shared instance regardless of how many chat stores exist (see
// createChatStore below).
export const audioEnabled = ref(false)
export const talkAvailable = ref(true)
export const micAvailable = ref(true)
export const spokenTextEnabled = ref(false)
// FIXME: null until GET /api/state resolves; kept separate from
// chatStoreFactory's `state` ref, which handleStateChange overwrites with
// a differently-shaped payload on every chat turn.
export const inputTokenBudgetPerTurn = ref(null)
// Same null-until-boot shape as inputTokenBudgetPerTurn above.
export const totalTokenBudgetPerSession = ref(null)

export function setCapabilities({ talkAvailable: talk, micAvailable: mic }) {
  talkAvailable.value = talk
  micAvailable.value = mic
}

export function setInputTokenBudgetPerTurn(value) {
  inputTokenBudgetPerTurn.value = value
}

export function setTotalTokenBudgetPerSession(value) {
  totalTokenBudgetPerSession.value = value
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
  confirmNewSession = true, useAutoTracking = false, useActuatorsToggle = false,
  subscribeToNotifications = false,
}) {
  const state = ref(null)
  const currentSessionId = ref(null)
  const selectedSessionActive = ref(false)
  const projectPaused = ref(false)
  const projectPausedReason = ref('')
  const sessions = ref([])
  const sessionsLoading = ref(false)
  const sessionsPanelOpen = ref(false)
  const currentProjectId = ref(null)
  const messages = ref([])
  const historyLoaded = ref(false)
  // How many turns are in flight right now: more than one is normal, the
  // input stays open while the model answers (see submitMessage), so a
  // user can send again before the previous reply lands.
  const turnsInFlight = ref(0)
  const chatLoading = computed(() => turnsInFlight.value > 0)
  const chatStatus = ref('')
  const actionLoading = ref(false)
  const autoTrackingEnabled = ref(true)
  const autoTrackingLoading = ref(false)
  const actuatorsEnabled = ref(false)
  const actuatorsLoading = ref(false)
  const draft = ref('')
  const turnCount = ref(0)
  let nextMessageId = 0

  registerSkinSource(kind, currentProjectId, currentSessionId)

  function bumpTurn() {
    turnCount.value++
  }

  // A fired action's own "on-enter" script is never part of a turn's
  // response: the backend runs it as a task and pushes its output over
  // the websocket, where notificationBus.js runs it once, globally.
  function handleStateChange(newState) {
    state.value = newState
  }

  new ChatReconnectSync({ currentSessionId, messages, state, toStoreMessage }).register()

  if (subscribeToNotifications) {
    // A server-pushed cross-project wake-up — can land for a project
    // other than the one currently open. Only applies state.value when
    // the notification is about the currently displayed project.
    subscribeToStateNotifications(({ project_name, state: newState }) => {
      if (project_name === currentProjectId.value) {
        handleStateChange(newState)
      }
    })
  }

  // Shapes a backend message row into what the chat UI renders — shared
  // by every place that (re)loads a session's full history from scratch.
  function toStoreMessage(m) {
    return {
      // Same local-id sequence a placeholder gets (see submitMessage below)
      // — never the backend's own m.id, which restarts from 1 per session
      // and would collide with a placeholder's counter value. `messageId`
      // (below) still carries the real backend id.
      id: ++nextMessageId,
      role: m.role, content: m.content, audioText: m.audio_text, reaction: m.reaction,
      timestamp: m.timestamp, failed: false, messageId: m.id,
      // The permanent "Searched <source> for ... · N rows" line(s) this
      // message's own tool call(s) left behind (see ChatService.get_messages'
      // own tool_calls_by_message) — undefined for a message with none.
      toolCalls: m.tool_calls ?? null
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

  async function loadActuators() {
    try {
      const res = await getActuators(currentSessionId.value)
      actuatorsEnabled.value = res.enabled
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
    if (session.legal_terms_pending) return null
    currentSessionId.value = session.id
    selectedSessionActive.value = session.active
    currentProjectId.value = session.project_id
    state.value = session.state
    if (useAutoTracking) await loadAutoTracking()
    if (useActuatorsToggle) await loadActuators()
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

  async function loadSessions(includeImported = false, projectId = null) {
    sessionsLoading.value = true
    try {
      sessions.value = await getSessionsList(includeImported, projectId ?? currentProjectId.value)
    } catch {
      // already surfaced via apiFetch
    } finally {
      sessionsLoading.value = false
    }
  }

  // Same fetch as loadSessions, but never touches sessionsLoading — for a
  // caller that wants `sessions` refreshed without flashing the panel to
  // "Loading…".
  async function refreshSessionsQuietly(includeImported = false, projectId = null) {
    try {
      sessions.value = await getSessionsList(includeImported, projectId ?? currentProjectId.value)
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
      // manual_actions is baked into state at fetch time (see ChatService.
      // _with_manual_actions) — the toggle just flipped which actions
      // that filter includes, so the already-loaded state is now stale.
      state.value = await getSessionState(currentSessionId.value)
    } catch {
      // already surfaced via apiFetch
    } finally {
      autoTrackingLoading.value = false
    }
  }

  async function toggleActuators() {
    actuatorsLoading.value = true
    try {
      const res = await postActuators(currentSessionId.value, !actuatorsEnabled.value)
      actuatorsEnabled.value = res.enabled
    } catch {
      // already surfaced via apiFetch
    } finally {
      actuatorsLoading.value = false
    }
  }

  function toggleAudio() {
    audioEnabled.value = !audioEnabled.value
    if (audioEnabled.value) {
      // Inside this same click gesture — every narration from here on,
      // including the one about to play below, happens well outside one.
      unlockAudioPlayback()
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

  function handleSessionInactiveError(err) {
    const shouldDeactivate = err.code ? SESSION_INACTIVE_CODES.includes(err.code) : err.status === 409
    if (!shouldDeactivate) return
    selectedSessionActive.value = false
    if (sessionsPanelOpen.value) loadSessions()
  }

  async function submitMessage(message) {
    clearApiError()
    setMessageFailed(message.id, false)
    turnsInFlight.value++

    // Snapshotted once, up front: this turn's own session, never re-read
    // off currentSessionId.value below. The AI provider can take a long
    // time to reply — long enough that the user switches to a completely
    // different chat before it's back. Without this, every completion
    // effect below (including the ones that decide which session is
    // "current") would run against whatever's on screen *then*, not the
    // session this turn was actually sent for — silently attributing a
    // stale reply to the wrong, now-open chat.
    const turnSessionId = currentSessionId.value

    // Create the assistant bubble up front, to receive chunks as they stream in
    const assistantMsgId = ++nextMessageId
    const assistantMsg = {
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      audioText: null,
      messageId: null,
      timestamp: new Date().toISOString(),
      // Backend-composed line (e.g. "Searching Flights…") shown in place
      // of the typing dots while a tool call is in flight — see
      // MessageBubble.vue's own isAwaitingReply branch. Cleared by the
      // matching tool_result event, no sooner than TOOL_STATUS_MIN_MS
      // after it was shown (see ToolStatusHold) — "done" never cuts it short.
      statusText: ''
    }
    messages.value.push(assistantMsg)
    const statusHold = new ToolStatusHold({
      setStatusText: (text) => {
        if (currentSessionId.value !== turnSessionId) return
        const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
        if (idx !== -1) messages.value[idx] = { ...messages.value[idx], statusText: text }
      }
    })

    // Set the moment onStatus first fires with a non-empty status_text
    // (a live tool_call event) — the signal that this turn's own
    // tool_calls are worth fetching once it's done, since a live SSE
    // turn never carries the persisted trace itself (see the
    // result.assistant_message_id branch below).
    let hadToolCall = false
    // Whether any real text chunk was ever applied to this bubble — once
    // true, the bubble must never be silently dropped again (see the
    // catch block below): a user who's already seen partial text must not
    // have it vanish just because the stream later failed.
    let hasChunk = false

    try {
      const result = await sendChatMessage(message.content, turnSessionId, {
        onStatus: (text) => {
          if (currentSessionId.value !== turnSessionId) return
          if (text) hadToolCall = true
          chatStatus.value = text
          if (text) statusHold.show(text)
          else statusHold.hide()
        },
        onChunk: (chunkText) => {
          if (currentSessionId.value !== turnSessionId) return
          hasChunk = true
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

      if (currentSessionId.value !== turnSessionId) {
        // The user has since switched to a different chat — this reply is
        // real and already persisted server-side, but has nothing to do
        // with whatever's on screen now. Applying any of the effects below
        // would leak session turnSessionId's own reply/state into the chat
        // actually being viewed (and silently revert currentSessionId back
        // to it — see the write below). Switching back to turnSessionId
        // re-fetches its real history from the server instead (selectSession).
        return
      }

      // Correlate this bubble with its real backend id — needed by
      // testTimeline.js's effectiveTimestamp to position a pre-turn
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

      // The turn's own persisted assistant message, in the same
      // {id, content, audio_text, timestamp} shape handleAction already
      // consumes (see ChatService._build_turn_response) — the one
      // reconciliation point for this bubble: content is *replaced* here
      // (never concatenated — a lost chunk earlier in the stream must not
      // leave a truncated prefix baked in), and the timestamp is the
      // server's own, not the client clock.
      const replyMsg = result.reply?.[0] ?? null
      let idx = messages.value.findIndex((m) => m.id === assistantMsgId)
      if (replyMsg) {
        const reconciled = {
          role: 'assistant',
          content: replyMsg.content,
          audioText: replyMsg.audio_text,
          messageId: replyMsg.id,
          timestamp: replyMsg.timestamp
        }
        if (idx !== -1) {
          messages.value[idx] = { ...messages.value[idx], ...reconciled }
        } else {
          // The bubble is gone — e.g. onVisibilityChange's own
          // reloadMessages replaced the whole list mid-turn. The reply is
          // real and already persisted; it must still show up rather than
          // silently vanish, so it's appended fresh at the end.
          messages.value.push({ id: assistantMsgId, ...reconciled })
          idx = messages.value.length - 1
        }
      } else if (result.assistant_message_id == null) {
        // No AI reply was generated this turn (e.g. a pre-turn transition
        // landed in a state that doesn't chat at all) — remove the empty,
        // orphaned bubble instead of leaving it.
        if (idx !== -1) messages.value.splice(idx, 1)
        idx = -1
      } else if (idx !== -1) {
        // Defensive fallback — an assistant_message_id with no matching
        // reply entry shouldn't happen, but the bubble must never be
        // dropped once it exists: keep whatever text it already streamed.
        messages.value[idx] = {
          ...messages.value[idx],
          messageId: result.assistant_message_id,
          timestamp: new Date().toISOString()
        }
      }

      statusHold.hide()

      // The live SSE turn only ever streamed status_text/chunks, never the
      // permanent tool-call trace itself (see toStoreMessage) — fetched
      // here, once, straight from what a reload would show, so the two
      // paths agree instead of the trace only ever appearing after a
      // reload. Keyed off result.assistant_message_id (not replyMsg,
      // which may be absent even though a tool call — and the message it
      // produced — really happened).
      if (hadToolCall && idx !== -1 && result.assistant_message_id != null) {
        const assistantBackendId = result.assistant_message_id
        getMessages(turnSessionId).then((history) => {
          if (currentSessionId.value !== turnSessionId) return
          const persisted = history.find((m) => m.id === assistantBackendId)
          if (!persisted?.tool_calls) return
          const toolCallsIdx = messages.value.findIndex((m) => m.id === assistantMsgId)
          if (toolCallsIdx !== -1) {
            messages.value[toolCallsIdx] = {
              ...messages.value[toolCallsIdx],
              toolCalls: persisted.tool_calls
            }
          }
        }).catch(() => {
          // Best-effort — the live trace is cosmetic; a manual reload
          // still shows it via the normal toStoreMessage path.
        })
      }

      playMessageChime()

      if (result.assistant_message_id != null) {
        maybeAutoPlayAudio(result.assistant_message_id)
      }

      if (result.state) {
        handleStateChange(result.state)
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
      // On send failure, drop the bubble only if it never showed any real
      // text — once a chunk has been applied, the user has already seen
      // it, so it stays (marked failed) rather than vanishing. A no-op if
      // the user's since switched chats (messages.value is a different
      // session's array by then, never containing assistantMsgId).
      statusHold.cancel()
      const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
      if (idx !== -1) {
        if (hasChunk) {
          messages.value[idx] = { ...messages.value[idx], failed: true, statusText: '' }
        } else {
          messages.value.splice(idx, 1)
        }
      }
      setMessageFailed(message.id, true)

      // Only the still-current chat's own "session went inactive" banner
      // should react to this — a stale turn's error has nothing to say
      // about whichever different session the user's now looking at.
      if (currentSessionId.value === turnSessionId) handleSessionInactiveError(err)
    } finally {
      // Unconditional, unlike everything above: a turn that started must
      // always be counted out again on completion — gating it on
      // turnSessionId would leave a switched-away-from chat counting a
      // turn nothing is ever going to finish.
      turnsInFlight.value--
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
    // Same staleness guard as submitMessage above — an action's own reply
    // can arrive well after the user's moved on to a different chat.
    const turnSessionId = currentSessionId.value
    try {
      const result = await postAction(actionName, turnSessionId)

      if (currentSessionId.value !== turnSessionId) return // see submitMessage's own comment on this check

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
      handleStateChange(result.state)
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
      if (currentSessionId.value === turnSessionId) handleSessionInactiveError(err)
    } finally {
      actionLoading.value = false
    }
  }

  function clearChatUi() {
    messages.value = []
    clearApiError()
    chatStatus.value = ''
    autoTrackingEnabled.value = true
    actuatorsEnabled.value = false
    // A project switch is exactly when "the current session" should be re-resolved.
    currentSessionId.value = null
    currentProjectId.value = null
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
      // session's very first transition — its on-enter arrives over the
      // websocket like any other, never in this response.
      const newState = await resetSession()
      state.value = null
      handleStateChange(newState)
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
      if (session.legal_terms_pending) {
        setApiError('This project’s terms have changed. Please reload the page to continue.')
        return
      }
      currentSessionId.value = session.id
      selectedSessionActive.value = session.active
      clearApiError()
      messages.value = []
      // A brand new session enters init_action.target through init_action
      // itself; its on-enter arrives over the websocket like any other.
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

  async function handleCloseSession() {
    if (currentSessionId.value == null) return
    try {
      const session = await postCloseSession(currentSessionId.value)
      selectedSessionActive.value = session.active
      if (sessionsPanelOpen.value) await loadSessions()
    } catch {
      // already surfaced via apiFetch
    }
  }

  return {
    state, currentSessionId, selectedSessionActive, projectPaused, projectPausedReason,
    sessions, sessionsLoading, sessionsPanelOpen, currentProjectId,
    messages, historyLoaded, chatLoading, chatStatus, actionLoading,
    autoTrackingEnabled, autoTrackingLoading, actuatorsEnabled, actuatorsLoading, draft, turnCount,
    handleStateChange, loadMessages, loadSessions, refreshSessionsQuietly, toggleSessionsPanel,
    selectSession, reloadMessages, handleTruncateFrom, handleDeleteSession, toggleAutoTracking, toggleActuators,
    toggleAudio, handleSend, handleVoiceMessage, handleResend, handleReact, handleAction,
    clearChatUi, handleReset: resetSession ? handleReset : null, handleNewSession, handleCloseSession,
  }
}

