import { ref } from 'vue'
import { getAiModels, postAiModelSelection } from './api.js'

// Which AI model the live chat runs on — app-wide, not per chat session:
// one selection for the whole page, shared by every chat store (see
// testChatStore.js for the "Test" chat's own equivalent over
// ai_test_service).
export const aiModels = ref([])
export const aiModelAuto = ref(true)
export const aiModelCurrentIndex = ref(0)
export const aiModelSelectionLoading = ref(false)

export function applyAiModelInfo(info) {
  aiModels.value = info.models
  aiModelAuto.value = info.auto
  aiModelCurrentIndex.value = info.current_index
}

export async function loadAiModels() {
  try {
    applyAiModelInfo(await getAiModels())
  } catch {
    // already surfaced via apiFetch
  }
}

export async function selectAiModel(index) {
  aiModelSelectionLoading.value = true
  try {
    applyAiModelInfo(await postAiModelSelection(index))
  } catch {
    // already surfaced via apiFetch
  } finally {
    aiModelSelectionLoading.value = false
  }
}

// Bundles the live-chat model state + its own select() into one object —
// ModelMenu.vue's default `modelStore` prop. testChatStore.js's
// testChatModelStore exposes the same shape for ai_test_service, so the
// component itself never needs to know which context it's in.
export const liveModelStore = {
  models: aiModels,
  auto: aiModelAuto,
  currentIndex: aiModelCurrentIndex,
  selectionLoading: aiModelSelectionLoading,
  select: selectAiModel,
  autoLabel: 'Auto-live',
}

