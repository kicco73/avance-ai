import { ref } from 'vue'
import {
  getTestSessions, postResetTestSessions,
  getTestChatModels, postTestChatModelSelection
} from './api.js'
import { createChatStore } from '../../chatStoreFactory.js'

export function setTestProject(name) {
  testStore.clearChatUi()
  testStore.setProject(name)
}

export function releaseTestProject() {
  testStore.clearChatUi()
}

export const testStore = createChatStore({
  kind: 'test',
  getSessionsList: () => getTestSessions(testStore.currentProjectId.value),
  resetSession: () => postResetTestSessions(testStore.currentProjectId.value),
  confirmNewSession: false,
  useActuatorsToggle: true,
  visualEffects: window.self !== window.top,
})

export const {
  state, currentSessionId, selectedSessionActive, blockedReason, blockedDetail,
  sessions, sessionsLoading, sessionsPanelOpen, currentProjectId,
  messages, historyLoaded, chatLoading, chatStatus, actionLoading,
  actuatorsEnabled, actuatorsLoading, draft, turnCount,
  setProject,
  handleStateChange, loadMessages, loadSessions, refreshSessionsQuietly, toggleSessionsPanel,
  selectSession, reloadMessages, handleTruncateFrom, handleDeleteSession, toggleActuators,
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
  }
}

async function selectTestChatModel(index) {
  testChatModelSelectionLoading.value = true
  try {
    applyTestChatModelInfo(await postTestChatModelSelection(index))
  } catch {
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
