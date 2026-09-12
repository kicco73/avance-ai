import { computed, nextTick, ref, watch } from 'vue'
import {
  getSessionState, getActuators, putActuators,
  putSessionAudio,
  postTruncateSession, deleteSession, postCloseSession, putMessageReaction,
} from './api.js'
import {
  sendMessage as sendChatMessage, sendButton, sendSessionOpened, onConnectionState, getConnectionState,
} from './chatClient.js'
import { busChannel } from './busChannel.js'
import { ChatReconnectSync } from './chatReconnectSync.js'
import { ChatExchange } from './chatExchange.js'
import { modelSelector } from './modelSelector.js'
import { watchSession } from './watchedSessions.js'
import { ToolStatusHold } from './toolStatusHold.js'
import { subscribeToStateNotifications } from './notificationBus.js'
import { playMessageChime, playReactionChime } from './audio.js'
import { audioEnabled } from './chatPreferences.js'
import { messageArrived } from './messageNotifier.js'
import { clearApiError, setApiError } from './errorStore.js'
import { confirmDialog } from './dialogStore.js'
import { registerSkinSource } from './chatSkin.js'

const SESSION_INACTIVE_CODES = ['session_closed', 'session_channel_mismatch', 'session_superseded']

// An empty 'output.text_stream' turns the dots on; if nothing
// real follows within this long, they turn back off on their own rather
// than sitting there forever (e.g. an operator who started typing then
// walked away) — the turn itself keeps waiting regardless, this only
// governs the dots.
const AWAITING_REPLY_TIMEOUT_MS = 15000

// The chat's one transport, as the UI sees it: 'connecting' | 'open' |
// 'closed' (see chatClient.js). Module-level, not per-store — there is
// exactly one socket per page, whichever chats are open on it.
export const chatConnectionState = ref(getConnectionState())
onConnectionState((next) => { chatConnectionState.value = next })

// App-wide user preferences — genuinely not "which chat" state, so a
// single shared instance regardless of how many chat stores exist (see
// createChatStore below); audioEnabled/spokenTextEnabled live in
// chatPreferences.js, re-exported here for the screens that read them
// alongside the rest of a store.
export { audioEnabled, spokenTextEnabled, toggleSpokenText } from './chatPreferences.js'
// FIXME: null until GET /api/state resolves; kept separate from
// chatStoreFactory's `state` ref, which handleStateChange overwrites with
// a differently-shaped payload on every chat turn.
export const inputTokenBudgetPerTurn = ref(null)
// Same null-until-boot shape as inputTokenBudgetPerTurn above.
export const totalTokenBudgetPerSession = ref(null)


export function setInputTokenBudgetPerTurn(value) {
  inputTokenBudgetPerTurn.value = value
}

export function setTotalTokenBudgetPerSession(value) {
  totalTokenBudgetPerSession.value = value
}

// One independent chat conversation's worth of state — the live chat and
// EditProjectView's embedded "Run" test chat each get their own instance,
// never sharing a session id/messages/automaton state with the other.
// `kind` ('live'|'test') only ever drives chatSkin.js's routing, nothing
// about session resolution itself.
export function createChatStore({
  kind, getCurrentSession, getSessionsList, createSession, getMessages, resetSession = null,
  getAutoTracking = null, putAutoTracking = null,
  confirmNewSession = true, useAutoTracking = false, useActuatorsToggle = false,
  subscribeToNotifications = false,
}) {
  const state = ref(null)
  const currentSessionId = ref(null)
  // What this store is showing, so whoever needs to know a session is
  // already on screen can ask without naming any store (see
  // watchedSessions.js).
  watch(currentSessionId, (now, before) => watchSession(now, before))
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

  // The exchanges still being watched, by the local id of the bubble each
  // is writing into. A reconnection settles them: their frames are gone
  // with the socket, and what they produced (if anything) is in the rows
  // about to be reloaded.
  const openExchanges = new Map()

  function settleOpenExchanges(rebuild) {
    for (const [bubbleId, open] of [...openExchanges.entries()]) {
      openExchanges.delete(bubbleId)
      open.stop()
      open.settle(rebuild({ sessionId: open.sessionId, text: open.text }))
    }
  }

  function bumpTurn() {
    turnCount.value++
  }

  // A fired action's own "task" script is never part of a turn's
  // response: the backend runs it as a task and pushes its output over
  // the websocket, where notificationBus.js runs it once, globally.
  function handleStateChange(newState) {
    state.value = newState
  }

  new ChatReconnectSync({ currentSessionId, messages, state, toStoreMessage, settleOpenExchanges }).register()

  // The buttons are the system's to say and this store's to show: applied
  // the moment 'ui.buttons' arrives, on top of whichever state is held,
  // never waiting for an exchange to finish (see chatClient.js).
  // The choices the conversation offers now: shown the moment they
  // arrive, never held until an exchange ends.
  busChannel.subscribe('ui.buttons', (frame) => {
    if (frame.session_id !== currentSessionId.value) return
    state.value = { ...(state.value ?? {}), manual_actions: frame.actions || [] }
  })

  // A whole message that no exchange is waiting for — what a choice
  // produced, or anything else the system says on its own. An exchange
  // that is writing takes its own answer (see submitMessage); this is
  // everything else.
  busChannel.subscribe('output.text', (frame) => {
    if (frame.session_id !== currentSessionId.value) return
    if (openExchanges.size > 0) return
    if (messages.value.some((m) => m.messageId === frame.assistant_message_id)) return
    messages.value.push({
      id: ++nextMessageId,
      role: 'assistant',
      content: frame.text,
      messageId: frame.assistant_message_id,
      timestamp: frame.timestamp ?? new Date().toISOString(),
      statusText: ''
    })
    playMessageChime()
    if (frame.assistant_message_id != null) maybeAutoPlayAudio(frame.assistant_message_id)
    bumpTurn()
  })

  // Where the conversation is now, said only when it moved. The choices
  // go with the state that offered them: they are gone until the system
  // says what this state offers (the `ui.buttons` that follows).
  busChannel.subscribe('state.changed', (frame) => {
    if (frame.session_id !== currentSessionId.value) return
    handleStateChange({ ...(frame.state ?? {}), manual_actions: [] })
  })

  // The model reacted to what the person said — a fact about that
  // message, carrying its id.
  busChannel.subscribe('output.reaction', (frame) => {
    if (frame.session_id !== currentSessionId.value) return
    const idx = messages.value.findIndex((m) => m.role === 'user' && m.messageId == null)
    if (idx !== -1) {
      messages.value[idx] = {
        ...messages.value[idx], messageId: frame.user_message_id, reaction: frame.reaction
      }
    }
    if (frame.reaction) playReactionChime()
  })

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
    selectedSessionActive.value = session.current
    currentProjectId.value = session.project_id
    state.value = session.state
    if (useAutoTracking) await loadAutoTracking()
    if (useActuatorsToggle) await loadActuators()
    await syncAudioPreference()
    // The conversation is open: whether it has something to say first is
    // the automaton's business, and what comes back is an ordinary
    // message (see backend docs/BUS.md's own session.new).
    sendSessionOpened(session.id)
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
    selectedSessionActive.value = session.current
    syncAudioPreference()
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
      const res = await putAutoTracking(currentSessionId.value, !autoTrackingEnabled.value)
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
      const res = await putActuators(currentSessionId.value, !actuatorsEnabled.value)
      actuatorsEnabled.value = res.enabled
    } catch {
      // already surfaced via apiFetch
    } finally {
      actuatorsLoading.value = false
    }
  }

  async function syncAudioPreference() {
    if (currentSessionId.value == null) return
    try {
      await putSessionAudio(currentSessionId.value, audioEnabled.value)
    } catch {
      // A preference the next turn re-sends; never worth an error toast.
    }
  }

  function toggleAudio() {
    audioEnabled.value = !audioEnabled.value
    syncAudioPreference()
    return audioEnabled.value
  }

  // Something in this build may turn a finished reply into sound. This
  // store does not know who, or whether anyone does.
  function maybeAutoPlayAudio(messageId) {
    if (messageId == null) return
    messageArrived(messageId)
  }

  // Replace, never mutate in place: `messages.value[i]` is the raw object
  // this closure was handed, not the reactive proxy Vue wraps around it,
  // so mutating it would silently never re-render.
  function patchBubble(localId, fields) {
    const idx = messages.value.findIndex((m) => m.id === localId)
    if (idx !== -1) messages.value[idx] = { ...messages.value[idx], ...fields }
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

  // What a person said, and what the system publishes about it. Nothing
  // is awaited: the bubble is pushed, the message is sent, and a
  // ChatExchange watches what comes back (see chatExchange.js).
  function submitMessage(message) {
    clearApiError()
    setMessageFailed(message.id, false)
    turnsInFlight.value++

    // Snapshotted once, up front: this exchange's own session, never
    // re-read off currentSessionId.value below. An answer can take long
    // enough that the user switches to a completely different chat before
    // it lands — without this, every effect below would run against
    // whatever is on screen *then*.
    const turnSessionId = currentSessionId.value

    // Pushed right away: a bubble must occupy its slot in messages.value
    // immediately (see chatStoreCoalescing.test.js), or a second send
    // arriving first would land its own ahead of this one. `pending` is
    // what keeps it invisible until something real arrives (see
    // MessageBubble.vue's own isPending).
    const assistantMsgId = ++nextMessageId
    messages.value.push({
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      audioText: null,
      messageId: null,
      timestamp: new Date().toISOString(),
      pending: true,
      awaitingReply: false,
      statusText: ''
    })

    const statusHold = new ToolStatusHold({
      setStatusText: (text) => {
        if (currentSessionId.value !== turnSessionId) return
        patchBubble(assistantMsgId, { statusText: text })
      }
    })

    const mine = () => currentSessionId.value === turnSessionId
    const exchange = new ChatExchange({
      sessionId: turnSessionId,
      bubble: {
        writing: () => {
          if (mine()) patchBubble(assistantMsgId, { pending: false, awaitingReply: true })
        },
        stopWaiting: () => {
          if (mine()) patchBubble(assistantMsgId, { awaitingReply: false })
        },
        append: (text) => {
          if (!mine()) return
          const current = messages.value.find((m) => m.id === assistantMsgId)
          if (!current) return
          // Covers the one case the empty piece never arrives: a human
          // operator whose reply wins the race against their own typing
          // signal (see talker/human_talker.py's own chat()).
          patchBubble(assistantMsgId, {
            content: current.content + text, pending: false, awaitingReply: false
          })
        },
        // The text to be spoken — the answer's spoken version, written
        // by the model alongside it (see backend docs/BUS.md's own
        // output.speech).
        spoken: (text) => {
          if (mine()) patchBubble(assistantMsgId, { audioText: text })
        },
        status: (text) => {
          if (!mine()) return
          chatStatus.value = text
          if (text) statusHold.show(text)
          else statusHold.hide()
        },
        said: (said) => finishExchange(said),
        failed: (frame) => failExchange(frame)
      }
    }).watch()

    openExchanges.set(assistantMsgId, {
      sessionId: turnSessionId,
      text: message.content,
      stop: () => exchange.stop(),
      // What a reconnection found in the reloaded rows, if anything: the
      // same shape an answer arrives in, so there is one way to finish.
      settle: (rebuilt) => {
        if (rebuilt) finishExchange({ id: rebuilt.id, content: rebuilt.content, audio_text: rebuilt.audio_text })
        else failExchange({ message: 'The chat connection dropped during this message.', code: 'chat_reconnected' })
      }
    })

    function done() {
      openExchanges.delete(assistantMsgId)
      turnsInFlight.value--
      chatStatus.value = ''
      exchange.stop()
    }

    function finishExchange(said) {
      done()
      if (!mine()) {
        // The user has since switched chats. The message is real and
        // persisted; switching back re-fetches it (selectSession).
        return
      }
      // One answer can cover more than one request: the coalescer takes
      // whatever arrived while a reply was being written, and every one
      // of those requests is answered by that same message (see
      // TurnService._already_answered_response). It is already on screen
      // — this request's own empty bubble is the one to drop.
      const alreadyShown = messages.value.some(
        (m) => m.messageId === said.id && m.id !== assistantMsgId
      )
      const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
      if (alreadyShown) {
        if (idx !== -1) messages.value.splice(idx, 1)
        statusHold.hide()
        bumpTurn()
        return
      }
      if (idx !== -1) {
        messages.value[idx] = {
          ...messages.value[idx],
          content: said.content,
          messageId: said.id,
          timestamp: said.timestamp ?? messages.value[idx].timestamp,
          pending: false,
          awaitingReply: false
        }
      } else {
        // The bubble is gone — e.g. onVisibilityChange's own
        // reloadMessages replaced the whole list. The message is real and
        // must still show up rather than silently vanish.
        messages.value.push({
          id: assistantMsgId, role: 'assistant', content: said.content, messageId: said.id,
          timestamp: said.timestamp ?? new Date().toISOString(), statusText: ''
        })
      }

      statusHold.hide()
      loadToolTrace(said.id)
      playMessageChime()
      if (said.id != null) maybeAutoPlayAudio(said.id)
      // An exchange always lands on a session it just touched — open by
      // definition.
      currentSessionId.value = turnSessionId
      selectedSessionActive.value = true
      if (sessionsPanelOpen.value) loadSessions()
      bumpTurn()
    }

    function failExchange(frame) {
      done()
      statusHold.cancel()
      setApiError(frame.message, frame.detail)
      // Drop the bubble only if it never showed any real text — once a
      // piece has been applied the user has already seen it, so it stays
      // (marked failed) rather than vanishing.
      const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
      if (idx !== -1) {
        if (exchange.hasChunk) {
          messages.value[idx] = { ...messages.value[idx], failed: true, statusText: '' }
        } else {
          messages.value.splice(idx, 1)
        }
      }
      setMessageFailed(message.id, true)
      if (mine()) handleSessionInactiveError({ code: frame.code })
    }

    // The permanent tool-call trace is never streamed (see toStoreMessage)
    // — fetched once, straight from what a reload would show, so the two
    // paths agree instead of the trace only appearing after a reload.
    function loadToolTrace(backendId) {
      if (!exchange.hadToolCall || backendId == null) return
      getMessages(turnSessionId).then((history) => {
        if (!mine()) return
        const persisted = history.find((m) => m.id === backendId)
        if (!persisted?.tool_calls) return
        patchBubble(assistantMsgId, { toolCalls: persisted.tool_calls })
      }).catch(() => {
        // Best-effort — the live trace is cosmetic; a reload still shows it.
      })
    }

    sendChatMessage(message.content, turnSessionId)
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

  // The placeholder a voice message occupies while something else turns
  // it into words: whoever captured the audio calls back with the text,
  // or says it came to nothing. What does the transcribing is not this
  // store's business and is not always installed.
  function beginVoiceMessage() {
    const message = { id: ++nextMessageId, role: 'user', content: '', failed: false, transcribing: true }
    messages.value.push(message)
    return {
      transcribed(text) {
        message.content = text
        message.transcribing = false
        return submitMessage(message)
      },
      abandoned() {
        dropVoicePlaceholder(message.id)
      },
    }
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

  // A choice taken. It travels the same road as what a person types
  // (`input.button` on the socket), and what it produces comes back the same
  // way — the new state's own message, what it offers next, where the
  // conversation is now. There is nothing here to await.
  function handleAction(actionName) {
    clearApiError()
    // Off while it is being said, gone once it has been: a choice can be
    // taken once, and what can be done next is the system's to say (the
    // `ui.buttons` that follows). If it never left — no socket — they
    // come back on, because nothing was taken.
    actionLoading.value = true
    const taken = sendButton(actionName, currentSessionId.value)
    actionLoading.value = false
    if (taken) state.value = { ...(state.value ?? {}), manual_actions: [] }
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
      // session's very first transition — its task arrives over the
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
      selectedSessionActive.value = session.current
      clearApiError()
      messages.value = []
      // A brand new session enters init_action.target through init_action
      // itself; its task arrives over the websocket like any other.
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
      selectedSessionActive.value = session.current
      if (sessionsPanelOpen.value) await loadSessions()
    } catch {
      // already surfaced via apiFetch
    }
  }

  return {
    settleOpenExchanges,
    state, currentSessionId, selectedSessionActive, projectPaused, projectPausedReason,
    sessions, sessionsLoading, sessionsPanelOpen, currentProjectId,
    messages, historyLoaded, chatLoading, chatStatus, actionLoading,
    autoTrackingEnabled, autoTrackingLoading, actuatorsEnabled, actuatorsLoading, draft, turnCount,
    handleStateChange, loadMessages, loadSessions, refreshSessionsQuietly, toggleSessionsPanel,
    selectSession, reloadMessages, handleTruncateFrom, handleDeleteSession, toggleAutoTracking, toggleActuators,
    toggleAudio, handleSend, beginVoiceMessage, handleResend, handleReact, handleAction,
    clearChatUi, handleReset: resetSession ? handleReset : null, handleNewSession, handleCloseSession,
  }
}

