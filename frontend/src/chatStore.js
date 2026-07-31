import { nextTick, ref } from 'vue'
import {
  getCurrentSession,
  postCreateSession,
  getMessages,
  postAction,
  getAutoTracking,
  postAutoTracking,
  getAiModels,
  postAiModelSelection,
  messageAudioUrl,
  postListenTranscribe,
  postReset
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

export function handleStateChange(newState) {
  const changed = state.value?.key !== newState?.key
  state.value = newState
  if (changed && newState?.on_enter === 'celebrate') {
    celebrate()
  }
}

async function ensureSession() {
  const session = await getCurrentSession(currentSessionId.value)
  currentSessionId.value = session.id
  return session.id
}

export async function loadMessages() {
  try {
    const sessionId = await ensureSession()
    const history = await getMessages(sessionId)
    messages.value = history.map((m) => ({
      role: m.role,
      content: m.content,
      audioText: m.audio_text,
      failed: false,
      messageId: m.id
    }))
  } catch {
    // already surfaced via apiFetch
  } finally {
    await nextTick()
    historyLoaded.value = true
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
  const assistantMsg = { id: assistantMsgId, role: 'assistant', content: '', audioText: null, messageId: null }
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
        messages.value.push({ role: 'assistant', content, audioText: audio_text, messageId: id })
      }
    }

    playMessageChime()

    if (result.reply && result.reply.length > 0) {
      maybeAutoPlayAudio(result.reply[result.reply.length - 1].id)
    }

    if (result.state) {
      handleStateChange(result.state)
    }
    if (result.ai_model) {
      applyAiModelInfo(result.ai_model)
    }
    if (result.session_id != null) {
      currentSessionId.value = result.session_id
    }
    bumpTurn()
  } catch (err) {
    // In caso di errore durante l'invio, rimuoviamo la bolla vuota/incompleta
    const idx = messages.value.findIndex((m) => m.id === assistantMsgId)
    if (idx !== -1) messages.value.splice(idx, 1)

    setMessageFailed(message.id, true)
  } finally {
    chatLoading.value = false
    chatStatus.value = ''
  }
}

export async function handleSend(text) {
  const message = { id: ++nextMessageId, role: 'user', content: text, failed: false }
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
      messages.value.push({ role: 'assistant', content, audioText: audio_text, messageId: id })
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
    }
    bumpTurn()
  } catch {
    // already surfaced via apiFetch
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
  try {
    const session = await postCreateSession()
    currentSessionId.value = session.id
    clearApiError()
    messages.value = []
    await loadMessages()
    bumpTurn()
  } catch {
    // already surfaced via apiFetch
  }
}