import { ref } from 'vue'
import {
  getTestSessions, postResetTestSessions,
  getTestChatModels, postTestChatModelSelection
} from './api.js'
import { createChatStore } from '../../chatStoreFactory.js'

// EditProjectView's embedded "Run" test chat — its own independent
// session/messages/state, scoped to whichever project is currently open
// for editing. Never touches the live chat's currentSessionId/messages
// (see chatStore.js's own liveStore) — the two used to share one set of
// refs, toggled by a testModeProjectId flag, which is exactly what let
// browsing an imported session (or anything else touching the shared
// refs) bleed into the live chat and vice versa.
export function setTestProject(name) {
  testStore.clearChatUi()
  testStore.setProject(name)
}

export const testStore = createChatStore({
  kind: 'test',
  getSessionsList: () => getTestSessions(testStore.currentProjectId.value),
  resetSession: () => postResetTestSessions(testStore.currentProjectId.value),
  confirmNewSession: false,
  useAutoTracking: true,
  useActuatorsToggle: true,
  subscribeToNotifications: false,
})

export const {
  state, currentSessionId, selectedSessionActive, blockedReason, blockedDetail,
  sessions, sessionsLoading, sessionsPanelOpen, currentProjectId,
  messages, historyLoaded, chatLoading, chatStatus, actionLoading,
  autoTrackingEnabled, autoTrackingLoading, actuatorsEnabled, actuatorsLoading, draft, turnCount,
  setProject,
  handleStateChange, loadMessages, loadSessions, refreshSessionsQuietly, toggleSessionsPanel,
  selectSession, reloadMessages, handleTruncateFrom, handleDeleteSession, toggleAutoTracking, toggleActuators,
  toggleAudio, handleSend, beginVoiceMessage, handleResend, handleReact, handleAction,
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
  autoLabel: 'Auto-test',
}
