import { getCurrentSession, postCreateSession, getSessions } from './api.js'
import { createChatStore } from './chatStoreFactory.js'

export {
  audioEnabled, talkAvailable, micAvailable, spokenTextEnabled, inputTokenBudgetPerTurn, totalTokenBudgetPerSession,
  aiModels, aiModelAuto, aiModelCurrentIndex, aiModelSelectionLoading,
  setCapabilities, setInputTokenBudgetPerTurn, setTotalTokenBudgetPerSession, loadAiModels, selectAiModel, toggleSpokenText,
  liveModelStore,
} from './chatStoreFactory.js'
export { applyAspect, invalidateSkin, setSkinCss } from './chatSkin.js'

// The app's one live chat — App.vue's own always-mounted widget. Never
// shares a session/messages/state with EditProjectView's embedded "Run"
// test chat (see testChatStore.js) — each is its own independent
// createChatStore() instance.
export const liveStore = createChatStore({
  kind: 'live',
  getCurrentSession: (sessionId) => getCurrentSession(sessionId),
  getSessionsList: (includeImported, projectId) => getSessions(projectId, includeImported),
  createSession: () => postCreateSession(),
  confirmNewSession: true,
  useAutoTracking: false,
  subscribeToNotifications: true,
})

export const {
  state, currentSessionId, selectedSessionActive, projectPaused, projectPausedReason,
  sessions, sessionsLoading, sessionsPanelOpen, currentProjectId,
  messages, historyLoaded, chatLoading, chatStatus, actionLoading,
  autoTrackingEnabled, autoTrackingLoading, draft, turnCount,
  handleStateChange, loadMessages, loadSessions, refreshSessionsQuietly, toggleSessionsPanel,
  selectSession, reloadMessages, handleTruncateFrom, handleDeleteSession, toggleAutoTracking,
  toggleAudio, handleSend, handleVoiceMessage, handleResend, handleReact, handleAction,
  clearChatUi, handleNewSession, handleCloseSession,
} = liveStore
