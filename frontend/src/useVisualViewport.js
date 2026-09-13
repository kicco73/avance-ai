
function isStandalone() {
  return navigator.standalone === true || window.matchMedia('(display-mode: standalone)').matches
}

function isInputFocused() {
  const el = document.activeElement
  if (!el) return false
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable
}

function updateOvershoot() {
  if (isInputFocused()) return
  const overshoot = Math.max(0, window.screen.height - window.innerHeight)
  document.documentElement.style.setProperty('--viewport-bottom-overshoot', `${overshoot}px`)
}

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

  document.addEventListener('focusout', () => {
    requestAnimationFrame(() => {
      if (!isInputFocused()) remeasureViewport()
    })
  })

  let lastHeight = window.visualViewport?.height ?? null
  window.visualViewport?.addEventListener('resize', () => {
    const vv = window.visualViewport
    const grew = lastHeight != null && vv.height > lastHeight
    lastHeight = vv.height
    if (grew && !isInputFocused()) remeasureViewport()
  })
}
