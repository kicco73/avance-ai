import { computed, onBeforeUnmount, ref } from 'vue'

const PREVIEW_EXPIRY_SECONDS = 5 * 60

const COUNTDOWN_THRESHOLD_SECONDS = 59

export function usePreviewExpiry() {
  const remainingSeconds = ref(PREVIEW_EXPIRY_SECONDS)
  const expired = ref(false)
  let interval = null

  const remainingLabel = computed(() => {
    const s = Math.max(0, remainingSeconds.value)
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  })

  const quitButtonLabel = computed(() => (
    remainingSeconds.value <= COUNTDOWN_THRESHOLD_SECONDS ? remainingLabel.value : 'Quit'
  ))

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

  return { remainingSeconds, remainingLabel, quitButtonLabel, expired, arm, clear }
}
