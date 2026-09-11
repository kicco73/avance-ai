import { computed, onBeforeUnmount, ref } from 'vue'

// The countdown that ends a try-it-out chat session — the app store's
// "Try me!" and Manage projects' own "Test" both run on it. A preview
// session holds a real automaton (and a real env) open, so it is never
// left running indefinitely just because the panel is still on screen.
//
// Why it reports expiry as a ref instead of calling back: the owner is
// what knows how to close its own preview (stop the session, tell the
// person it ended), and a timer that reaches into that through a callback
// would end up owning half of it. This only ever counts, and `expired`
// flipping to true is the whole of what it says.
const PREVIEW_EXPIRY_SECONDS = 5 * 60

// Below this, the Quit button turns into the countdown itself rather than
// showing a timer nobody is watching for the first four minutes.
const COUNTDOWN_THRESHOLD_SECONDS = 59

export function usePreviewExpiry() {
  const remainingSeconds = ref(PREVIEW_EXPIRY_SECONDS)
  const expired = ref(false)
  let interval = null

  const remainingLabel = computed(() => {
    const s = Math.max(0, remainingSeconds.value)
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  })

  // A timer that was never armed sits at the full window, well above the
  // threshold, so an untimed preview reads 'Quit' without needing to ask
  // whether there is a timer running at all.
  const quitButtonLabel = computed(() => (
    remainingSeconds.value <= COUNTDOWN_THRESHOLD_SECONDS ? remainingLabel.value : 'Quit'
  ))

  function clear() {
    if (interval === null) return
    clearInterval(interval)
    interval = null
  }

  // Starts, or restarts, the full window — a preview that is restarted in
  // place gets its whole five minutes again, not the remainder.
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
