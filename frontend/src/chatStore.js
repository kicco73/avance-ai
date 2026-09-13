import { getSessions } from './api.js'
import { createChatStore } from './chatStoreFactory.js'

export {
  audioEnabled, spokenTextEnabled, inputTokenBudgetPerTurn, totalTokenBudgetPerSession,
  setInputTokenBudgetPerTurn, setTotalTokenBudgetPerSession, toggleSpokenText,
} from './chatStoreFactory.js'
export { applyAspect, invalidateSkin, setSkinCss } from './chatSkin.js'

// The app's one live chat — App.vue's own always-mounted widget. Never
// shares a session/messages/state with EditProjectView's embedded "Run"
// test chat (see testChatStore.js) — each is its own independent
// createChatStore() instance.
export const liveStore = createChatStore({
  kind: 'live',
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
