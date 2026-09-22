import { computed, nextTick, ref, unref, watch } from 'vue'
import {
  getActuators, putActuators,
  getHistory, postTruncateSession, deleteSession,
} from './api.js'
import { busChannel } from './busChannel.js'
import { publishServices } from './skillServices.js'
import { ChatReconnectSync } from './chatReconnectSync.js'
import { ChatExchange } from './chatExchange.js'
import { modelSelector } from './modelSelector.js'
import { watchSession } from './watchedSessions.js'
import { ToolStatusHold } from './toolStatusHold.js'
import { playMessageChime, playReactionChime } from './audio.js'
import { audioEnabled } from './chatPreferences.js'
import { messageArrived } from './messageNotifier.js'
import { clearApiError, setApiError } from './errorStore.js'
import { notify } from './toastStore.js'
import { confirmDialog } from './dialogStore.js'
import { registerSkinSource } from './chatSkin.js'
import { runTaskScript } from './taskActions.js'
import { createBackgroundAudio } from './backgroundAudio.js'
import { rememberBackgroundAudio, recallBackgroundAudio, forgetBackgroundAudio } from './backgroundAudioSessionMemory.js'

const SESSION_INACTIVE_CODES = ['session_closed', 'session_channel_mismatch', 'session_superseded']
const TOAST_ONLY_ERRORS = { ai_provider_busy: () => notify('AI providers currently busy', 'Please try again.') }

function showFrameError(frame) {
  const toastOnly = TOAST_ONLY_ERRORS[frame.code]
  if (toastOnly) { toastOnly(); return }
  setApiError(frame.message, frame.detail)
}

export const chatConnectionState = ref(busChannel.connectionState)
busChannel.onConnectionState((next) => { chatConnectionState.value = next })

export { audioEnabled, spokenTextEnabled, toggleSpokenText } from './chatPreferences.js'
// FIXME: null until GET /api/state resolves; kept separate from
export const inputTokenBudgetPerTurn = ref(null)
export const totalTokenBudgetPerSession = ref(null)


export function setInputTokenBudgetPerTurn(value) {
  inputTokenBudgetPerTurn.value = value
}

export function setTotalTokenBudgetPerSession(value) {
  totalTokenBudgetPerSession.value = value
}

export function createChatStore({
  kind, channel = null, getSessionsList, resetSession = null,
  confirmNewSession = true, useActuatorsToggle = false, visualEffects = true,
}) {
  const state = ref(null)
  const currentSessionId = ref(null)
  watch(currentSessionId, (now, before) => watchSession(now, before))
  const {
    backgroundAudioUrl, backgroundAudioPlaying,
    playBackgroundAudio, stopBackgroundAudio, pauseBackgroundAudio, toggleBackgroundAudio,
  } = createBackgroundAudio()
  const selectedSessionActive = ref(false)
  const sessionEndReason = ref(null)
  const sessionChannel = ref(null)
  let replySilenceSeconds = null
  const conversationElsewhere = computed(() => {
    const mine = unref(channel)
    return !!mine && !!sessionChannel.value && sessionChannel.value !== mine
  })
  const blockedReason = ref(null)
  const blockedDetail = ref('')
  const sessions = ref([])
  const sessionsLoading = ref(false)
  const sessionsPanelOpen = ref(false)
  const currentProjectId = ref(null)
  const messages = ref([])
  const historyLoaded = ref(false)
  const turnsInFlight = ref(0)
  const chatLoading = computed(() => turnsInFlight.value > 0)
  const chatStatus = ref('')
  const actionLoading = ref(false)
  const actuatorsEnabled = ref(false)
  const actuatorsLoading = ref(false)
  const draft = ref('')
  const buttons = ref([])
  function showButtons(actions) {
    buttons.value = actions
    actionLoading.value = false
  }
  const chart = ref(null)
  function dismissChart() {
    chart.value = null
  }
  const turnCount = ref(0)
  let nextMessageId = 0

  registerSkinSource(kind, currentProjectId, currentSessionId)

  const openExchanges = new Map()

  function abandonOpenReplies() {
    for (const open of [...openExchanges.values()]) open.abandon()
  }

  function bumpTurn() {
    turnCount.value++
  }

  function handleStateChange(newState) {
    state.value = newState
  }

  new ChatReconnectSync({
    abandonOpenReplies() { abandonOpenReplies() },
    reenter() { enterSession('session.enter') },
  }).register()

  let awaitingSession = false

  function isAboutOurConversation(frame) {
    if (frame.session_id != null && frame.session_id === currentSessionId.value) return true
    if (!awaitingSession) return false
    if (frame.session_type != null && frame.session_type !== kind) return false
    return frame.project_id == null || frame.project_id === currentProjectId.value
  }

  busChannel.subscribe('session.info', (frame) => {
    if (!isAboutOurConversation(frame)) return
    if (currentSessionId.value != null && frame.session_id !== currentSessionId.value) stopBackgroundAudio()
    if (frame.session_id !== currentSessionId.value && !backgroundAudioUrl.value) {
      const rememberedUrl = recallBackgroundAudio(kind, frame.session_id)
      if (rememberedUrl) playBackgroundAudio(rememberedUrl)
    }
    awaitingSession = false
    blockedReason.value = null
    blockedDetail.value = ''
    currentSessionId.value = frame.session_id
    currentProjectId.value = frame.project_id ?? currentProjectId.value
    selectedSessionActive.value = frame.current ?? true
    sessionEndReason.value = null
    sessionChannel.value = frame.channel ?? null
    replySilenceSeconds = frame.reply_silence_seconds
    state.value = frame.state
    audioEnabled.value = !!frame.audio
    publishServices(frame.services || {})
    if (useActuatorsToggle) loadActuators()
    if (sessionsPanelOpen.value) loadSessions()
  })

  busChannel.subscribe('session.messages', (frame) => {
    if (frame.session_id !== currentSessionId.value) return
    messages.value = (frame.messages || []).map(toStoreMessage)
    settleHistory()
  })

  busChannel.subscribe('session.blocked', (frame) => {
    if (!isAboutOurConversation(frame)) return
    stopBackgroundAudio()
    awaitingSession = false
    currentSessionId.value = null
    state.value = null
    messages.value = []
    showButtons([])
    blockedReason.value = frame.reason || 'no_project'
    blockedDetail.value = frame.detail || ''
    settleHistory()
  })

  busChannel.subscribe('session.ended', (frame) => {
    if (frame.session_id !== currentSessionId.value) return
    stopBackgroundAudio()
    forgetBackgroundAudio(kind, frame.session_id)
    selectedSessionActive.value = false
    sessionEndReason.value = frame.reason ?? null
    if (sessionsPanelOpen.value) loadSessions()
  })

  busChannel.subscribe('state.buttons', (frame) => {
    if (frame.session_id !== currentSessionId.value) return
    showButtons(frame.actions || [])
  })

  busChannel.subscribe('output.text_stream', (frame) => {
    if (frame.session_id !== currentSessionId.value) return
    if (openExchanges.size > 0) return
    watchReply(frame.session_id).receive(frame)
  })

  busChannel.subscribe('output.progress', (frame) => {
    if (!isAboutOurConversation(frame)) return
    if (openExchanges.size > 0) return
    watchReply(frame.session_id).receive(frame)
  })

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

  busChannel.subscribe('output.error', (frame) => {
    if (frame.session_id !== currentSessionId.value) return
    if (openExchanges.size > 0) return
    actionLoading.value = false
    showFrameError(frame)
  })

  busChannel.subscribe('state.changed', (frame) => {
    if (frame.session_id !== currentSessionId.value) return
    showButtons([])
    handleStateChange(frame.state ?? {})
  })

  busChannel.subscribe('ui.notification', (frame) => {
    if (!visualEffects) return
    if (!frame.task) return
    if (!(frame.session_id == null ? frame.project_id === currentProjectId.value : isAboutOurConversation(frame))) return
    runTaskScript(frame.task, {
      playBackgroundAudio: (url) => {
        playBackgroundAudio(url)
        rememberBackgroundAudio(kind, frame.session_id ?? currentSessionId.value, url)
      },
    })
  })

  busChannel.subscribe('output.reaction', (frame) => {
    if (frame.session_id !== currentSessionId.value) return
    const idx = messages.value.findLastIndex((m) => m.role === 'user' && m.messageId == null)
    if (idx !== -1) {
      messages.value[idx] = {
        ...messages.value[idx], messageId: frame.user_message_id, reaction: frame.reaction
      }
    }
    if (frame.reaction) playReactionChime()
  })

  busChannel.subscribe('output.chart', (frame) => {
    if (!isAboutOurConversation(frame)) return
    chart.value = { title: frame.title, series: frame.series || [], maxScale: frame.max_scale }
  })

  function toStoreMessage(m) {
    return {
      id: ++nextMessageId,
      role: m.role, content: m.content, audioText: m.audio_text, reaction: m.reaction,
      timestamp: m.timestamp, failed: false, messageId: m.id,
      toolCalls: m.tool_calls ?? null
    }
  }

  async function loadActuators() {
    try {
      const res = await getActuators(currentSessionId.value)
      actuatorsEnabled.value = res.enabled
    } catch {
    }
  }

  async function settleHistory() {
    await nextTick()
    historyLoaded.value = true
  }

  function setProject(projectId) {
    currentProjectId.value = projectId
  }

  function enterSession(type) {
    if (currentProjectId.value == null) return
    blockedReason.value = null
    blockedDetail.value = ''
    awaitingSession = true
    busChannel.send({ type, project_id: currentProjectId.value, session_type: kind })
  }

  async function loadMessages(projectId = currentProjectId.value) {
    currentProjectId.value = projectId
    historyLoaded.value = false
    if (projectId == null) {
      blockedReason.value = 'no_project'
      blockedDetail.value = ''
      await settleHistory()
      return
    }
    enterSession('session.enter')
  }

  async function loadSessions(includeImported = false, projectId = null) {
    sessionsLoading.value = true
    try {
      sessions.value = await getSessionsList(includeImported, projectId ?? currentProjectId.value)
    } catch {
    } finally {
      sessionsLoading.value = false
    }
  }

  async function refreshSessionsQuietly(includeImported = false, projectId = null) {
    try {
      sessions.value = await getSessionsList(includeImported, projectId ?? currentProjectId.value)
    } catch {
    }
  }

  async function toggleSessionsPanel() {
    sessionsPanelOpen.value = !sessionsPanelOpen.value
    if (sessionsPanelOpen.value) {
      await loadSessions()
    }
  }

  async function selectSession(session) {
    if (session.id === currentSessionId.value) return
    stopBackgroundAudio()
    currentSessionId.value = session.id
    selectedSessionActive.value = session.current
    sessionChannel.value = session.channel ?? null
    syncAudioPreference()
    messages.value = []
    historyLoaded.value = false
    busChannel.send({ type: 'session.enter', session_id: session.id, session_type: kind })
  }

  async function reloadMessages() {
    if (currentSessionId.value == null) return
    try {
      messages.value = (await getHistory(currentSessionId.value)).map(toStoreMessage)
    } catch {
    }
  }

  async function handleTruncateFrom(timestamp) {
    if (currentSessionId.value == null) return
    try {
      const newState = await postTruncateSession(currentSessionId.value, timestamp)
      await reloadMessages()
      state.value = null
      handleStateChange(newState)
      bumpTurn()
    } catch {
    }
  }

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
    }
  }

  async function toggleActuators() {
    actuatorsLoading.value = true
    try {
      const res = await putActuators(currentSessionId.value, !actuatorsEnabled.value)
      actuatorsEnabled.value = res.enabled
    } catch {
    } finally {
      actuatorsLoading.value = false
    }
  }

  function syncAudioPreference() {
    if (currentSessionId.value == null) return
    busChannel.send({
      type: 'session.speak', session_id: currentSessionId.value, enabled: audioEnabled.value,
    })
  }

  function toggleAudio() {
    audioEnabled.value = !audioEnabled.value
    syncAudioPreference()
    return audioEnabled.value
  }

  function maybeAutoPlayAudio(messageId) {
    if (messageId == null) return
    messageArrived(messageId)
  }

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

  function submitMessage(message) {
    clearApiError()
    setMessageFailed(message.id, false)
    const sent = busChannel.send({
      type: 'input.text', session_id: currentSessionId.value, text: message.content,
    })
    if (!sent) setMessageFailed(message.id, true)
  }

  function watchReply(turnSessionId) {
    turnsInFlight.value++

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
      statusText: '',
      progressTitle: '',
      progressPercentage: null
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
      silenceSeconds: replySilenceSeconds,
      bubble: {
        writing: () => {
          if (mine()) patchBubble(assistantMsgId, { pending: false, awaitingReply: true })
        },
        append: (text) => {
          if (!mine()) return
          const current = messages.value.find((m) => m.id === assistantMsgId)
          if (!current) return
          patchBubble(assistantMsgId, {
            content: current.content + text, pending: false, awaitingReply: false
          })
        },
        spoken: (text) => {
          if (mine()) patchBubble(assistantMsgId, { audioText: text })
        },
        status: (text) => {
          if (!mine()) return
          chatStatus.value = text
          if (text) statusHold.show(text)
          else statusHold.hide()
        },
        progress: (title, percentage) => {
          if (!mine()) return
          patchBubble(assistantMsgId, { pending: false, progressTitle: title, progressPercentage: percentage })
        },
        said: (said) => finishExchange(said),
        failed: (frame) => failExchange(frame)
      }
    }).watch()

    openExchanges.set(assistantMsgId, { sessionId: turnSessionId, abandon: done })

    function done() {
      openExchanges.delete(assistantMsgId)
      turnsInFlight.value--
      chatStatus.value = ''
      exchange.stop()
    }

    function finishExchange(said) {
      done()
      if (!mine()) {
        return
      }
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
          awaitingReply: false,
          progressPercentage: null
        }
      } else {
        messages.value.push({
          id: assistantMsgId, role: 'assistant', content: said.content, messageId: said.id,
          timestamp: said.timestamp ?? new Date().toISOString(), statusText: '', progressPercentage: null
        })
      }

      statusHold.hide()
      loadToolTrace(said.id)
      playMessageChime()
      if (said.id != null) maybeAutoPlayAudio(said.id)
      currentSessionId.value = turnSessionId
      selectedSessionActive.value = true
      if (sessionsPanelOpen.value) loadSessions()
      bumpTurn()
    }

    function failExchange(frame) {
      done()
      statusHold.cancel()
      actionLoading.value = false
      showFrameError(frame)
      markUnansweredFailed()
      const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
      if (idx !== -1) {
        if (exchange.hasChunk) {
          messages.value[idx] = { ...messages.value[idx], failed: true, statusText: '', progressPercentage: null }
        } else {
          messages.value.splice(idx, 1)
        }
      }
      if (mine()) handleSessionInactiveError({ code: frame.code })
    }

    function markUnansweredFailed() {
      for (let i = messages.value.length - 1; i >= 0; i--) {
        const m = messages.value[i]
        if (m.id === assistantMsgId) continue
        if (m.role !== 'user') break
        m.failed = true
      }
    }

    function loadToolTrace(backendId) {
      if (!exchange.hadToolCall || backendId == null) return
      getHistory(turnSessionId).then((history) => {
        if (!mine()) return
        const persisted = history.find((m) => m.id === backendId)
        if (!persisted?.tool_calls) return
        patchBubble(assistantMsgId, { toolCalls: persisted.tool_calls })
      }).catch(() => {
      })
    }

    return exchange
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

  function beginVoiceMessage() {
    const message = { id: ++nextMessageId, role: 'user', content: '', failed: false, transcribing: true }
    messages.value.push(message)
    return {
      transcribed(text) {
        patchBubble(message.id, { content: text, transcribing: false })
        return submitMessage({ ...message, content: text, transcribing: false })
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

  function handleReact(messageId, reaction) {
    const message = messages.value.find((m) => m.messageId === messageId)
    if (!message || message.role !== 'assistant') return
    const sent = busChannel.send({
      type: 'input.reaction', session_id: currentSessionId.value,
      assistant_message_id: messageId, reaction,
    })
    if (!sent) return
    message.reaction = reaction
    if (reaction) playReactionChime()
  }

  function handleAction(actionName) {
    clearApiError()
    const taken = busChannel.send({
      type: 'input.button', session_id: currentSessionId.value, id: actionName,
    })
    actionLoading.value = taken
    if (!taken) setApiError('Nothing was sent.', 'The chat is not connected.')
  }

  function clearChatUi() {
    messages.value = []
    showButtons([])
    dismissChart()
    clearApiError()
    chatStatus.value = ''
    actuatorsEnabled.value = false
    blockedReason.value = null
    blockedDetail.value = ''
    currentSessionId.value = null
    currentProjectId.value = null
    selectedSessionActive.value = true
    sessions.value = []
    stopBackgroundAudio()
  }

  async function handleReset() {
    const ok = await confirmDialog({
      title: 'Reset conversation',
      body: 'Reset the conversation, signals, and transitions? This cannot be undone.',
      okLabel: 'Reset',
      danger: true
    })
    if (!ok) return
    const projectId = currentProjectId.value
    clearChatUi()
    setProject(projectId)
    try {
      const newState = await resetSession()
      state.value = null
      handleStateChange(newState)
      await loadMessages()
      bumpTurn()
    } catch {
    }
  }

  async function handleNewSession() {
    if (confirmNewSession) {
      const ok = await confirmDialog({
        title: 'Start new session',
        body: 'Start a new session? This will close the current session — only one can be active at a time.',
        okLabel: 'Start'
      })
      if (!ok) return
    }
    clearApiError()
    messages.value = []
    showButtons([])
    historyLoaded.value = false
    sessionsPanelOpen.value = true
    enterSession('session.create')
    bumpTurn()
  }

  function handleCloseSession() {
    if (currentSessionId.value == null) return
    busChannel.send({ type: 'session.terminate', session_id: currentSessionId.value })
  }

  return {
    abandonOpenReplies,
    state, currentSessionId, selectedSessionActive, sessionEndReason, sessionChannel, conversationElsewhere,
    blockedReason, blockedDetail,
    backgroundAudioUrl, backgroundAudioPlaying, toggleBackgroundAudio, stopBackgroundAudio, pauseBackgroundAudio,
    sessions, sessionsLoading, sessionsPanelOpen, currentProjectId,
    messages, historyLoaded, chatLoading, chatStatus, actionLoading, buttons,
    chart, dismissChart,
    actuatorsEnabled, actuatorsLoading, draft, turnCount,
    setProject,
    handleStateChange, loadMessages, loadSessions, refreshSessionsQuietly, toggleSessionsPanel,
    selectSession, reloadMessages, handleTruncateFrom, handleDeleteSession, toggleActuators,
    toggleAudio, handleSend, beginVoiceMessage, handleResend, handleReact, handleAction,
    clearChatUi, handleReset: resetSession ? handleReset : null, handleNewSession, handleCloseSession,
  }
}

