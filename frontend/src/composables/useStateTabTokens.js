import { onBeforeUnmount, ref, watch } from 'vue'
import { getStateInputTokens } from '../api.js'
import { onProjectChanged } from '../projectChangeEvents.js'

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
  // An edit can change what a state's turn costs without changing which
  // state is selected, so the key alone is not enough to watch.
  onBeforeUnmount(onProjectChanged((changedProjectId) => {
    if (changedProjectId === projectId) return refreshStateTabTokens()
  }))

  return { stateTabTokens }
}
