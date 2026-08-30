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
// Scoped to standalone mode specifically — this bug is exclusive to iOS's
// own standalone (added-to-home-screen) mode; a regular browser tab, and
// non-iOS platforms, don't have it and don't need any of this.

function isStandalone() {
  // navigator.standalone is iOS Safari's own long-standing flag; iOS 26
  // added a second way a home-screen webapp can report the same mode,
  // display-mode: standalone, and some iOS 26 devices only expose it that
  // way — checking both is what actually catches every case there.
  return navigator.standalone === true || window.matchMedia('(display-mode: standalone)').matches
}

function isInputFocused() {
  const el = document.activeElement
  if (!el) return false
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable
}

// How far window.innerHeight falls short of window.screen.height — on
// standalone iOS this is exactly the shortfall left by WebKit bug #301108
// (see index.html's own viewport meta comment: viewport-fit=cover stops
// the layout viewport short of the home indicator instead of extending
// under it). 0 on a device that doesn't have the bug, and 0 again once/if
// Apple fixes it — this workaround retires itself automatically rather
// than needing a version check. Skipped while a field is focused: the
// keyboard shrinks innerHeight by hundreds of px, an entirely different
// (and already handled — see installViewportRecovery below) situation,
// not a deficit to compensate for.
function updateOvershoot() {
  if (isInputFocused()) return
  const overshoot = Math.max(0, window.screen.height - window.innerHeight)
  document.documentElement.style.setProperty('--viewport-bottom-overshoot', `${overshoot}px`)
}

// Every full-viewport container's own bottom reads
// calc(-1 * var(--viewport-bottom-overshoot, 0px)) instead of the 0 an
// inset: 0 box would use, extending that far past the (short) viewport's
// own bottom edge — position: fixed elements don't contribute to
// scrollable overflow, so this paints into the gap without making the
// page scrollable.
export function installViewportOvershoot() {
  if (!isStandalone()) return
  updateOvershoot()
  window.addEventListener('resize', updateOvershoot)
  window.addEventListener('orientationchange', updateOvershoot)
}

function remeasureViewport() {
  const el = document.getElementById('app')
  if (!el) return
  el.style.display = 'none'
  void el.offsetHeight
  el.style.display = ''
  updateOvershoot()
}

export function installViewportRecovery() {
  if (!isStandalone()) return

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
