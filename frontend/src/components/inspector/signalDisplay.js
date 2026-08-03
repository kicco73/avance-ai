import { ref } from 'vue'

const FLASH_MS = 900

// A signal has a displayable value only when it's been computed and came
// back clean — shared by every UI that renders a signal's live value bar
export function hasSignalValue(signal) {
  return signal != null && signal.value !== null && !signal.error
}

// Tracks which signals just changed value, for a brief flash animation on
// their bar (see each caller's own `-changed` CSS class/keyframes) — the
// one place this diffing lives, shared by every UI that displays live
// signal values.
export function useSignalChangeFlash() {
  // Reassigned wholesale (never mutated in place) so Vue's reactivity
  // picks up each change.
  const recentlyChanged = ref(new Set())
  let resetHandle = null

  // Compares `nextSignals` against `previousSignals` — the caller must
  // capture `previousSignals` before overwriting its own signal state,
  // since that's the only "old" copy available. A signal with no prior
  // value (first load, or was in error) never flashes: there's nothing to
  // visibly change from. Both arrays are just {name, value, error}.
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
