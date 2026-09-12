import { getSessions } from './api.js'
import { liveChatChannel } from './liveChatChannel.js'
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
  getCurrentSession: (sessionId) => liveChatChannel().getCurrentSession(sessionId),
  getSessionsList: (includeImported, projectId) => getSessions(projectId, includeImported),
  createSession: () => liveChatChannel().createSession(),
  getMessages: (sessionId) => liveChatChannel().getMessages(sessionId),
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
  toggleAudio, handleSend, beginVoiceMessage, handleResend, handleReact, handleAction,
  clearChatUi, handleNewSession, handleCloseSession,
} = liveStore
