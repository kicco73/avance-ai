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

// Substituted for App.vue's own --safe-area-*-fallback custom properties
// when env(safe-area-inset-*) measures 0 on standalone iOS (see
// installSafeAreaFallback below, and index.html's own viewport meta
// comment for why that can happen at all now). Tunable — these are just
// the current values Apple ships: 59pt is the top inset on a Dynamic
// Island device, 34pt is the standard home indicator bottom inset.
const SAFE_AREA_TOP_FALLBACK_PX = 59
const SAFE_AREA_BOTTOM_FALLBACK_PX = 34

// Reads env(safe-area-inset-top)/env(safe-area-inset-bottom) the only way
// they're actually readable — CSS custom environment variables have no
// JS-facing equivalent, so this bounces them through an offscreen probe
// element's own computed style instead. Fixed positioning + a large
// negative offset keeps the probe from ever affecting layout or paint;
// removed immediately after the one read it exists for.
function measureSafeAreaInsets() {
  const probe = document.createElement('div')
  probe.style.position = 'fixed'
  probe.style.top = '-9999px'
  probe.style.left = '-9999px'
  probe.style.paddingTop = 'env(safe-area-inset-top)'
  probe.style.paddingBottom = 'env(safe-area-inset-bottom)'
  document.body.appendChild(probe)
  const computed = getComputedStyle(probe)
  const top = parseFloat(computed.paddingTop) || 0
  const bottom = parseFloat(computed.paddingBottom) || 0
  probe.remove()
  return { top, bottom }
}

// index.html deliberately no longer sets viewport-fit=cover (see its own
// comment on why) — env(safe-area-inset-*) is spec'd to read 0 without
// that token, which would otherwise collapse every reservation built on
// App.vue's own --safe-area-* custom properties (the input row landing
// back on the rounded corners/home indicator, exactly what those existed
// to prevent). Only sets a fallback for whichever edge actually measured
// 0 — a device that does still report a real inset (or a future iOS that
// fixes this without the token) keeps using that real value untouched.
export function installSafeAreaFallback() {
  if (!isStandalone()) return
  const { top, bottom } = measureSafeAreaInsets()
  const root = document.documentElement.style
  if (top === 0) root.setProperty('--safe-area-top-fallback', `${SAFE_AREA_TOP_FALLBACK_PX}px`)
  if (bottom === 0) root.setProperty('--safe-area-bottom-fallback', `${SAFE_AREA_BOTTOM_FALLBACK_PX}px`)
}

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
