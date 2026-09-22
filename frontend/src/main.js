import { createApp } from 'vue'
import App from './App.vue'
import './styles/base.css'
import './styles/markdownContent.css'
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
  setInterval(() => {
    if (!queue.length) return
    const batch = queue.splice(0, queue.length)
    fetch('/__fx', { method: 'POST', body: JSON.stringify(batch) })
  }, 400)
}

