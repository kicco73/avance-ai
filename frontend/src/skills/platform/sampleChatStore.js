import { computed, ref, watch } from 'vue'
import { getAppPreviewTranscript } from './api.js'

export const SAMPLE_MESSAGES = [
  { id: 'sample-1', messageId: 'sample-1', role: 'assistant', content: 'Hi! How can I help you today?', timestamp: new Date().toISOString() },
  { id: 'sample-2', messageId: 'sample-2', role: 'user', content: 'I have a question about my order.', timestamp: new Date().toISOString() },
  { id: 'sample-3', messageId: 'sample-3', role: 'assistant', content: 'Sure — what would you like to know?', timestamp: new Date().toISOString() }
]

const SAMPLE_BUTTONS = [
  { name: 'sample-action-1', ui_button: 'Track my order', has_trigger: false },
  { name: 'sample-action-2', ui_button: 'Talk to a human', has_trigger: false, disabled: true }
]

const noop = () => {}

export function createSampleChatStore({ stateKey = ref(''), appId = ref(null) } = {}) {
  const messages = ref(SAMPLE_MESSAGES)
  const historyLoaded = ref(true)

  async function loadTranscript() {
    const asked = appId.value
    if (!asked) {
      messages.value = SAMPLE_MESSAGES
      return
    }
    historyLoaded.value = false
    try {
      const res = await getAppPreviewTranscript(asked)
      if (appId.value !== asked) return
      messages.value = res.messages?.length
        ? res.messages.map((m) => ({ id: m.id, messageId: m.id, role: m.role, content: m.content, timestamp: m.timestamp }))
        : SAMPLE_MESSAGES
    } catch {
      if (appId.value === asked) messages.value = SAMPLE_MESSAGES
    } finally {
      if (appId.value === asked) historyLoaded.value = true
    }
  }

  watch(appId, loadTranscript, { immediate: true })

  return {
    sample: true,
    state: computed(() => ({ key: stateKey.value, chat_enabled: true, reactions: [] })),
    buttons: ref(SAMPLE_BUTTONS),
    messages,
    historyLoaded,
    chatLoading: ref(false),
    chatStatus: ref(''),
    actionLoading: ref(false),
    draft: ref(''),
    currentSessionId: ref(null),
    selectedSessionActive: ref(true),
    sessionEndReason: ref(null),
    conversationElsewhere: ref(false),
    blockedReason: ref(null),
    blockedDetail: ref(''),
    audioEnabled: ref(false),
    handleNewSession: noop,
    handleCloseSession: noop,
    handleSend: noop,
    handleResend: noop,
    handleReact: noop,
    handleAction: noop,
    toggleAudio: noop,
    reloadMessages: noop
  }
}
