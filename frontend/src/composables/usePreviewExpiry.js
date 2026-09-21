import { computed, onBeforeUnmount, ref } from 'vue'

const PREVIEW_EXPIRY_SECONDS = 5 * 60

export function usePreviewExpiry() {
  const remainingSeconds = ref(PREVIEW_EXPIRY_SECONDS)
  const expired = ref(false)
  let interval = null

  const remainingLabel = computed(() => {
    const s = Math.max(0, remainingSeconds.value)
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  })

  const elapsedPercent = computed(() => {
    const s = Math.min(PREVIEW_EXPIRY_SECONDS, Math.max(0, remainingSeconds.value))
    return Math.round(((PREVIEW_EXPIRY_SECONDS - s) / PREVIEW_EXPIRY_SECONDS) * 100)
  })

  function clear() {
    if (interval === null) return
    clearInterval(interval)
    interval = null
  }

  function arm() {
    clear()
    expired.value = false
    remainingSeconds.value = PREVIEW_EXPIRY_SECONDS
    interval = setInterval(() => {
      remainingSeconds.value -= 1
      if (remainingSeconds.value > 0) return
      clear()
      expired.value = true
    }, 1000)
  }

  onBeforeUnmount(clear)

  return { remainingSeconds, remainingLabel, elapsedPercent, expired, arm, clear }
}
