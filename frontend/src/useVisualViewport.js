// iOS standalone home-screen webapps (see index.html's apple-mobile-web-app-*
// meta tags) have a long-documented WebKit bug: the layout viewport shrinks
// the first time the on-screen keyboard opens, and never resets when it
// closes. 100vh, 100dvh, position: fixed; inset: 0, and even
// window.innerHeight all keep reporting that same shrunk height afterwards —
// on every screen, not just the one the keyboard was open on — until the app
// is force-quit and relaunched; no CSS unit or JS measurement can see past a
// viewport stuck like that. The only known fix is forcing WebKit to actually
// re-measure it, which remeasureViewport() below does by briefly removing
// #app from layout (display: none) and putting it back.
//
// Scoped to navigator.standalone specifically — this bug is exclusive to
// iOS's own standalone (added-to-home-screen) mode; a regular browser tab,
// and non-iOS platforms, don't have it and don't need any of this.

function isInputFocused() {
  const el = document.activeElement
  if (!el) return false
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable
}

function remeasureViewport() {
  const el = document.getElementById('app')
  if (!el) return
  el.style.display = 'none'
  void el.offsetHeight
  el.style.display = ''
}

export function installViewportRecovery() {
  if (navigator.standalone !== true) return

  // focusout fires the instant a field loses focus, but the keyboard
  // itself (and the viewport shrink recovering) animates shut over the
  // next few frames — check on the frame after this one, once
  // document.activeElement has actually settled on whatever comes next
  // (another field, or nothing).
  document.addEventListener('focusout', () => {
    requestAnimationFrame(() => {
      if (!isInputFocused()) remeasureViewport()
    })
  })

  // Belt-and-suspenders for the same recovery, keyed off the visual
  // viewport itself rather than focus: a *growing* height is the
  // keyboard closing (shrinking is it opening, which must never trigger
  // this — remeasuring mid-open would fight the animation). Guarded by
  // isInputFocused() too, since a grow can also come from switching to a
  // field with a shorter keyboard (e.g. numeric) while still typing,
  // which isn't a real recovery moment.
  let lastHeight = window.visualViewport?.height ?? null
  window.visualViewport?.addEventListener('resize', () => {
    const vv = window.visualViewport
    const grew = lastHeight != null && vv.height > lastHeight
    lastHeight = vv.height
    if (grew && !isInputFocused()) remeasureViewport()
  })
}
