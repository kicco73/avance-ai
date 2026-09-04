import {
  getCurrentPreviewSession, postCreatePreviewSession, deleteSession, deletePreviewSessionEnv,
} from './api.js'
import { createChatStore } from './chatStoreFactory.js'

let appId = null
export function setPreviewApp(id) {
  appId = id
}

export const appStorePreviewStore = createChatStore({
  kind: 'appStorePreview',
  getCurrentSession: (sessionId) => getCurrentPreviewSession(sessionId, appId),
  getSessionsList: () => Promise.resolve([]),
  createSession: () => postCreatePreviewSession(appId),
  confirmNewSession: false,
  useAutoTracking: false,
  useActuatorsToggle: false,
  subscribeToNotifications: false,
})

export const {
  state, currentSessionId, selectedSessionActive, projectPaused, projectPausedReason,
  messages, historyLoaded, chatLoading, chatStatus, actionLoading, draft,
  handleSend, handleVoiceMessage, handleResend, handleReact, handleAction, toggleAudio,
  loadMessages, clearChatUi, handleNewSession,
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
