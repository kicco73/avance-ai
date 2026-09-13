import { ref } from 'vue'
import { getAiModels, postAiModelSelection } from './api.js'

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
  }
}

export async function selectAiModel(index) {
  aiModelSelectionLoading.value = true
  try {
    applyAiModelInfo(await postAiModelSelection(index))
  } catch {
  } finally {
    aiModelSelectionLoading.value = false
  }
}

export const liveModelStore = {
  available: true,
  models: aiModels,
  auto: aiModelAuto,
  currentIndex: aiModelCurrentIndex,
  selectionLoading: aiModelSelectionLoading,
  select: selectAiModel,
  load: loadAiModels,
  applyInfo: applyAiModelInfo,
  autoLabel: 'Auto-live',
}

