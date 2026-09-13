import { getSessions } from './api.js'
import { createChatStore } from './chatStoreFactory.js'
import { liveChatChannel } from './liveChatChannel.js'

export {
  audioEnabled, spokenTextEnabled, inputTokenBudgetPerTurn, totalTokenBudgetPerSession,
  setInputTokenBudgetPerTurn, setTotalTokenBudgetPerSession, toggleSpokenText,
} from './chatStoreFactory.js'
export { applyAspect, invalidateSkin } from './chatSkin.js'

export const liveStore = createChatStore({
  kind: 'live',
  channel: liveChatChannel,
  getSessionsList: (includeImported, projectId) => getSessions(projectId, includeImported),
  confirmNewSession: true,
  useAutoTracking: false,
  subscribeToNotifications: true,
})

export const {
  state, currentSessionId, selectedSessionActive, blockedReason, blockedDetail,
  sessions, sessionsLoading, sessionsPanelOpen, currentProjectId,
  messages, historyLoaded, chatLoading, chatStatus, actionLoading,
  autoTrackingEnabled, autoTrackingLoading, draft, turnCount,
  setProject,
  handleStateChange, loadMessages, loadSessions, refreshSessionsQuietly, toggleSessionsPanel,
  selectSession, reloadMessages, handleTruncateFrom, handleDeleteSession, toggleAutoTracking,
  toggleAudio, handleSend, beginVoiceMessage, handleResend, handleReact, handleAction,
  clearChatUi, handleNewSession, handleCloseSession,
} = liveStore
