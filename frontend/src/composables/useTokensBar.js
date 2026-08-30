import { computed, unref } from 'vue'

// Shared by every token-usage bar (InspectorDetailCard.vue's per-state
// estimate, ProjectTestPanel.vue's per-run budget): green under 75% of
// max, orange under 100%, red at/above it — the fill itself is capped at
// max (never overflows the track) even when the real count is higher.
// FIXME: `max` may be a ref, not just a number — unref'd below.
export function useTokensBar(tokens, max) {
  const width = computed(() => {
    const cap = unref(max)
    return `${Math.min(tokens.value ?? 0, cap) / cap * 100}%`
  })
  const level = computed(() => {
    const cap = unref(max)
    const value = tokens.value ?? 0
    if (value >= cap) return 'red'
    if (value >= cap * 0.75) return 'orange'
    return 'green'
  })
  return { width, level }
}
