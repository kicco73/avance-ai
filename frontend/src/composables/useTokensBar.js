import { computed } from 'vue'

// Shared by every token-usage bar (InspectorDetailCard.vue's per-state
// estimate, ProjectTestPanel.vue's per-run budget): green under 75% of
// max, orange under 100%, red at/above it — the fill itself is capped at
// max (never overflows the track) even when the real count is higher.
export function useTokensBar(tokens, max) {
  const width = computed(() => `${Math.min(tokens.value ?? 0, max) / max * 100}%`)
  const level = computed(() => {
    const value = tokens.value ?? 0
    if (value >= max) return 'red'
    if (value >= max * 0.75) return 'orange'
    return 'green'
  })
  return { width, level }
}
