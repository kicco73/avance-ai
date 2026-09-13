import { deleteSession, deletePreviewSessionEnv } from './api.js'
import { createChatStore } from '../../chatStoreFactory.js'

export function setPreviewApp(id) {
  appStorePreviewStore.setProject(id)
}

export const appStorePreviewStore = createChatStore({
  kind: 'preview',
  getSessionsList: () => Promise.resolve([]),
  confirmNewSession: false,
  useAutoTracking: false,
  useActuatorsToggle: false,
  subscribeToNotifications: false,
})

export const {
  state, currentSessionId, selectedSessionActive, blockedReason, blockedDetail,
  messages, historyLoaded, chatLoading, chatStatus, actionLoading, draft,
  handleSend, beginVoiceMessage, handleResend, handleReact, handleAction, toggleAudio,
  loadMessages, clearChatUi, handleNewSession, setProject,
} = appStorePreviewStore

export async function stopPreviewSession() {
  const sessionId = currentSessionId.value
  clearChatUi()
  historyLoaded.value = false
  if (sessionId != null) {
    try {
      await deletePreviewSessionEnv(sessionId)
      await deleteSession(sessionId)
    } catch {
      // already surfaced via apiFetch
    }
  }
}

export async function restartPreviewSession() {
  const sessionId = currentSessionId.value
  if (sessionId != null) {
    try {
      await deletePreviewSessionEnv(sessionId)
    } catch {
      // already surfaced via apiFetch
    }
  }
  await handleNewSession()
}
