import { onBeforeUnmount, onMounted, ref } from 'vue'

// window.visualViewport tracks the portion of the page actually visible —
// it shrinks and pans under the on-screen keyboard, and again under a
// voluntary pinch-zoom, neither of which touch the *layout* viewport that
// plain CSS units (vh, %, position: fixed + inset) stay anchored to. That
// mismatch is what strands part of the chat under the keyboard or off
// past a pinch-zoomed edge with no way back short of a reload.
//
// A single shared listener (ref-counted below, since LiveChatWindow.vue's
// own ChatView and RunChat.vue's can be mounted at once) keeps one
// reactive height/offsetTop pair and mirrors them as CSS custom
// properties on <html> — --visual-viewport-height and
// --visual-viewport-offset-top — so any component's own CSS can read them
// with e.g. var(--visual-viewport-height, 100dvh) without needing a
// listener of its own. Falls back to null when visualViewport isn't
// supported at all; callers fall back to 100dvh in that case.
const height = ref(window.visualViewport?.height ?? null)
const offsetTop = ref(window.visualViewport?.offsetTop ?? 0)

let listenerCount = 0

function update() {
  const vv = window.visualViewport
  if (!vv) return
  height.value = vv.height
  offsetTop.value = vv.offsetTop
  const root = document.documentElement.style
  root.setProperty('--visual-viewport-height', `${vv.height}px`)
  root.setProperty('--visual-viewport-offset-top', `${vv.offsetTop}px`)
}

// iOS 26 has an active WebKit bug where visualViewport.height/offsetTop
// don't settle to their correct values right as the keyboard opens or
// closes (https://bugs.webkit.org/show_bug.cgi?id=259770 and multiple
// open reports) — a 'resize' event fires, but reading the viewport right
// then can still give the stale pre-transition numbers, which is what
// stranded a visible gap above LiveChatWindow.vue's own fixed window
// until the user dragged the screen. The empirically reliable nudge
// (used as a workaround elsewhere for this same bug) is a 1px scroll and
// back, which forces Safari to recompute; doing that a frame after every
// resize and re-reading afterward gets the real, settled numbers without
// requiring the user's own manual scroll to trigger it.
function settle() {
  update()
  requestAnimationFrame(() => {
    window.scrollBy(0, 1)
    window.scrollBy(0, -1)
    update()
  })
}

export function useVisualViewport() {
  onMounted(() => {
    listenerCount++
    if (listenerCount > 1 || !window.visualViewport) return
    window.visualViewport.addEventListener('resize', settle)
    window.visualViewport.addEventListener('scroll', settle)
    update()
  })

  onBeforeUnmount(() => {
    listenerCount--
    if (listenerCount > 0 || !window.visualViewport) return
    window.visualViewport.removeEventListener('resize', settle)
    window.visualViewport.removeEventListener('scroll', settle)
  })

  return { height, offsetTop }
}
