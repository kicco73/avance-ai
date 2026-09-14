import { createApp } from 'vue'
import App from './App.vue'
import './styles/base.css'
import { installViewportOvershoot, installViewportRecovery } from './useVisualViewport.js'

installViewportOvershoot()
installViewportRecovery()
createApp(App).mount('#app')

if (import.meta.env.DEV) {
  const queue = [{ t: 0, kind: 'LOAD', name: location.href, el: navigator.userAgent.slice(0, 60) }]
  const describe = (el) => {
    if (!(el instanceof Element)) return String(el)
    const path = []
    let node = el
    while (node && path.length < 4) {
      path.unshift(node.tagName.toLowerCase() + (node.className && typeof node.className === 'string' ? '.' + node.className.trim().split(/\s+/).join('.') : ''))
      node = node.parentElement
    }
    return path.join(' > ')
  }
  const record = (kind) => (event) => {
    const el = event.target
    const style = el instanceof Element ? getComputedStyle(el) : null
    queue.push({
      t: Math.round(performance.now()),
      kind,
      name: event.animationName ?? event.propertyName,
      el: describe(el),
      transform: style?.transform,
      opacity: style?.opacity,
      duration: style?.transitionDuration + '/' + style?.animationDuration
    })
  }
  document.addEventListener('animationstart', record('animation'), true)
  document.addEventListener('transitionrun', record('transition'), true)
  document.addEventListener('click', (event) => {
    const button = event.target.closest?.('button')
    if (button) queue.push({ t: Math.round(performance.now()), kind: 'CLICK', name: button.textContent.trim().slice(0, 30), el: describe(button) })
  }, true)
  window.addEventListener('error', (e) => {
    queue.push({ t: Math.round(performance.now()), kind: 'ERROR', name: String(e.message), el: (e.error && e.error.stack) ? e.error.stack.slice(0, 600) : '' })
  })
  window.addEventListener('unhandledrejection', (e) => {
    queue.push({ t: Math.round(performance.now()), kind: 'REJECT', name: String(e.reason && e.reason.message ? e.reason.message : e.reason), el: (e.reason && e.reason.stack) ? e.reason.stack.slice(0, 600) : '' })
  })
  const originalConsoleError = console.error
  console.error = (...args) => {
    queue.push({ t: Math.round(performance.now()), kind: 'CONSOLE', name: args.map((a) => (a && a.stack) ? a.stack.slice(0, 600) : String(a)).join(' | ').slice(0, 900), el: '' })
    originalConsoleError(...args)
  }
  const originalConsoleWarn = console.warn
  console.warn = (...args) => {
    queue.push({ t: Math.round(performance.now()), kind: 'CONSOLEWARN', name: args.map((a) => String(a)).join(' | ').slice(0, 900), el: '' })
    originalConsoleWarn(...args)
  }

  document.addEventListener('click', () => {
    setTimeout(() => {
      const preview = document.querySelector('.manage-projects-preview')
      const list = document.querySelector('.manage-projects-list')
      const body = document.querySelector('.manage-projects-body')
      const probe = (el) => {
        if (!el) return 'absent'
        const r = el.getBoundingClientRect()
        const cs = getComputedStyle(el)
        return `w=${Math.round(r.width)} h=${Math.round(r.height)} x=${Math.round(r.x)} display=${cs.display} overflow=${cs.overflow} vis=${cs.visibility} opacity=${cs.opacity}`
      }
      queue.push({
        t: Math.round(performance.now()),
        kind: 'PROBE',
        name: `body[${probe(body)}] list[${probe(list)}] preview[${probe(preview)}]`,
        el: preview ? `children=${preview.children.length} :: ` + [...preview.children].map((c) => c.className + '#' + Math.round(c.getBoundingClientRect().width) + 'x' + Math.round(c.getBoundingClientRect().height)).join(' | ').slice(0, 400) : ''
      })
    }, 300)
  }, true)

  let probedOnce = false
  const measure = (tag) => {
    const preview = document.querySelector('.manage-projects-preview')
    const probe = (el) => {
      if (!el) return 'absent'
      const r = el.getBoundingClientRect()
      const cs = getComputedStyle(el)
      return `w=${Math.round(r.width)} h=${Math.round(r.height)} y=${Math.round(r.y)} op=${cs.opacity} pos=${cs.position} trans=${cs.transitionDuration}`
    }
    queue.push({
      t: Math.round(performance.now()),
      kind: 'MPPROBE',
      name: `${tag} visibility=${document.visibilityState} hidden=${document.hidden} preview[${probe(preview)}]`,
      el: preview ? `kids=${preview.children.length} :: ` + [...preview.children].map((c) => c.className + ' #' + probe(c)).join(' || ').slice(0, 700) : 'no preview'
    })
  }
  const probeManageProjects = () => {
    const row = document.querySelector('.manage-projects-row')
    if (!row || probedOnce) return
    probedOnce = true
    setTimeout(() => {
      measure('before-click')
      row.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      setTimeout(() => measure('t+600ms'), 600)
      setTimeout(() => measure('t+4000ms'), 4000)
    }, 2500)
  }
  new MutationObserver(probeManageProjects).observe(document.documentElement, { childList: true, subtree: true })

  const probeBase = import.meta.env.VITE_API_URL ?? '/api'
  fetch(probeBase + '/skills/platform/app-store/apps', { credentials: 'include' })
    .then(async (r) => {
      const text = await r.text()
      queue.push({ t: 0, kind: 'APPSTORE', name: `status=${r.status} len=${text.length}`, el: text.slice(0, 500) })
    })
    .catch((e) => queue.push({ t: 0, kind: 'APPSTORE', name: 'THREW ' + e.message, el: '' }))

  setInterval(() => {
    if (!queue.length) return
    const batch = queue.splice(0, queue.length)
    fetch('/__fx', { method: 'POST', body: JSON.stringify(batch) })
  }, 400)
}

