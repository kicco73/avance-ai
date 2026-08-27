import { ref } from 'vue'
import { getTestModels, postTestModelSelection } from './api.js'

// Independent from chatStoreFactory.js's live-chat model state — this one
// talks to ai_test_service (GET/POST /api/projects/{p}/tests/models*),
// which the "Test" panel's own run-launch section controls.
export const testModels = ref([])
export const testModelAuto = ref(true)
export const testModelCurrentIndex = ref(0)
export const testModelSelectionLoading = ref(false)

function applyTestModelInfo(info) {
  testModels.value = info.models
  testModelAuto.value = info.auto
  testModelCurrentIndex.value = info.current_index
}

export async function loadTestModels(projectName) {
  try {
    applyTestModelInfo(await getTestModels(projectName))
  } catch {
    // already surfaced via apiFetch
  }
}

async function selectTestModel(projectName, index) {
  testModelSelectionLoading.value = true
  try {
    applyTestModelInfo(await postTestModelSelection(projectName, index))
  } catch {
    // already surfaced via apiFetch
  } finally {
    testModelSelectionLoading.value = false
  }
}

// ModelMenu.vue's `modelStore` prop shape — see chatStoreFactory.js's
// own liveModelStore for the chat-side counterpart. `projectName` is
// bound in by whoever constructs this (the Test panel knows its own
// project), so `select` here always matches ModelMenu's `select(index)` call.
export function makeTestModelStore(projectName) {
  return {
    models: testModels,
    auto: testModelAuto,
    currentIndex: testModelCurrentIndex,
    selectionLoading: testModelSelectionLoading,
    select: (index) => selectTestModel(projectName, index),
  }
}
