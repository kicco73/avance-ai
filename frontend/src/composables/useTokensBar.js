import { computed, unref } from 'vue'

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
