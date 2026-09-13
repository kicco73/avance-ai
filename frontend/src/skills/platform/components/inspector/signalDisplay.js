import { ref } from 'vue'

const FLASH_MS = 900

export function hasSignalValue(signal) {
  return signal != null && signal.value !== null && !signal.error
}

export function useSignalChangeFlash() {
  const recentlyChanged = ref(new Set())
  let resetHandle = null

  function markChanged(previousSignals, nextSignals) {
    const changed = new Set()
    for (const next of nextSignals) {
      const prev = previousSignals.find((s) => s.name === next.name)
      if (prev && !prev.error && !next.error && prev.value !== next.value) {
        changed.add(next.name)
      }
    }
    if (!changed.size) return
    recentlyChanged.value = changed
    clearTimeout(resetHandle)
    resetHandle = setTimeout(() => { recentlyChanged.value = new Set() }, FLASH_MS)
  }

  function dispose() {
    clearTimeout(resetHandle)
  }

  return { recentlyChanged, markChanged, dispose }
}
