import { ref } from 'vue'
import {
  getCurrentTestSession, postCreateTestSession, getTestSessions, postResetTestSessions,
  getTestChatModels, postTestChatModelSelection
} from './api.js'
import { createChatStore } from './chatStoreFactory.js'

// EditProjectView's embedded "Run" test chat — its own independent
// session/messages/state, scoped to whichever project is currently open
// for editing. Never touches the live chat's currentSessionId/messages
// (see chatStore.js's own liveStore) — the two used to share one set of
// refs, toggled by a testModeProjectName flag, which is exactly what let
// browsing an imported session (or anything else touching the shared
// refs) bleed into the live chat and vice versa.
let projectName = null
export function setTestProject(name) {
  projectName = name
}

export const testStore = createChatStore({
  kind: 'test',
  getCurrentSession: (sessionId) => getCurrentTestSession(sessionId, projectName),
  getSessionsList: () => getTestSessions(projectName),
  createSession: () => postCreateTestSession(projectName),
  resetSession: () => postResetTestSessions(projectName),
  confirmNewSession: false,
  useAutoTracking: true,
  subscribeToNotifications: false,
})

export const {
  state, currentSessionId, selectedSessionActive, projectPaused, projectPausedReason,
  sessions, sessionsLoading, sessionsPanelOpen, currentProjectName,
  messages, historyLoaded, chatLoading, chatStatus, actionLoading,
  autoTrackingEnabled, autoTrackingLoading, draft, turnCount,
  handleStateChange, loadMessages, loadSessions, refreshSessionsQuietly, toggleSessionsPanel,
  selectSession, reloadMessages, handleTruncateFrom, handleDeleteSession, toggleAutoTracking,
  toggleAudio, handleSend, handleVoiceMessage, handleResend, handleReact, handleAction,
  clearChatUi, handleReset, handleNewSession,
} = testStore

export const testChatModels = ref([])
export const testChatModelAuto = ref(true)
export const testChatModelCurrentIndex = ref(0)
export const testChatModelSelectionLoading = ref(false)

function applyTestChatModelInfo(info) {
  testChatModels.value = info.models
  testChatModelAuto.value = info.auto
  testChatModelCurrentIndex.value = info.current_index
}

export async function loadTestChatModels() {
  try {
    applyTestChatModelInfo(await getTestChatModels())
  } catch {
    // already surfaced via apiFetch
  }
}

async function selectTestChatModel(index) {
  testChatModelSelectionLoading.value = true
  try {
    applyTestChatModelInfo(await postTestChatModelSelection(index))
  } catch {
    // already surfaced via apiFetch
  } finally {
    testChatModelSelectionLoading.value = false
  }
}

export const testChatModelStore = {
  models: testChatModels,
  auto: testChatModelAuto,
  currentIndex: testChatModelCurrentIndex,
  selectionLoading: testChatModelSelectionLoading,
  select: selectTestChatModel,
}
