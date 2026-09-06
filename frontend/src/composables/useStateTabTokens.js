import { ref, watch } from 'vue'
import { getStateInputTokens } from '../api.js'

export function useStateTabTokens(projectId, stateKey) {
  const stateTabTokens = ref(null)
  let requestSeq = 0

  async function refreshStateTabTokens() {
    const key = stateKey.value
    if (!key) {
      stateTabTokens.value = null
      return
    }
    const requestId = ++requestSeq
    try {
      const { tokens } = await getStateInputTokens(projectId, key)
      if (requestId === requestSeq) stateTabTokens.value = tokens
    } catch {
      if (requestId === requestSeq) stateTabTokens.value = null
    }
  }

  watch(stateKey, refreshStateTabTokens, { immediate: true })

  return { stateTabTokens, refreshStateTabTokens }
}
