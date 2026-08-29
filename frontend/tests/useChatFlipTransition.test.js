import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { useChatFlipTransition } from '../src/composables/useChatFlipTransition.js'

// These tests verify the deterministic wiring (which inline styles get set,
// which event is listened for, when done() fires) — not visual smoothness
// or timing feel, which no jsdom test can meaningfully judge. This code is
// covered by [[feedback_js_hook_transitions_for_dynamic_direction]]: it was
// tuned against real rendering, so a manual check in an actual browser is
// still expected before trusting any change here.
describe('useChatFlipTransition', () => {
  let navDirection, appBody, rafCallbacks

  beforeEach(() => {
    vi.useFakeTimers()
    navDirection = ref('forward')
    appBody = document.createElement('div')
    appBody.className = 'app-body'
    appBody.style.setProperty('--flip-duration', '250ms')
    document.body.appendChild(appBody)
    rafCallbacks = []
    vi.stubGlobal('requestAnimationFrame', (cb) => { rafCallbacks.push(cb); return rafCallbacks.length })
  })

  afterEach(() => {
    appBody.remove()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  function flushRaf() {
    const pending = rafCallbacks.splice(0)
    pending.forEach((cb) => cb())
  }

  function mount() {
    return useChatFlipTransition(navDirection)
  }

  it('reads --flip-duration off .app-body, falling back to 500ms if unset/invalid', () => {
    const s = mount()
    const el = document.createElement('div')

    s.onChatLeave(el, () => {})
    expect(el.style.transition).toBe('transform 250ms ease-in-out')

    appBody.style.setProperty('--flip-duration', '')
    const el2 = document.createElement('div')
    s.onChatLeave(el2, () => {})
    expect(el2.style.transition).toBe('transform 500ms ease-in-out')
  })

  it('onChatBeforeEnter sets the initial rotated, transition-less state', () => {
    const s = mount()
    const el = document.createElement('div')

    s.onChatBeforeEnter(el)

    expect(el.style.transition).toBe('none')
    expect(el.style.backfaceVisibility).toBe('hidden')
    expect(el.style.zIndex).toBe('101')
    expect(el.style.transform).toBe('rotateY(-90deg)')
  })

  it('onChatEnter waits a tick, then animates to flat and calls done() once the transform transition ends', async () => {
    const s = mount()
    const el = document.createElement('div')
    const done = vi.fn()

    s.onChatEnter(el, done)
    expect(el.style.transition).toBe('') // not yet — waits `duration` first

    await vi.advanceTimersByTimeAsync(250)
    expect(el.style.transition).toBe('transform 250ms ease-out')
    flushRaf()
    expect(el.style.transform).toBe('rotateY(0deg)')

    el.dispatchEvent(new TransitionEvent('transitionend', { propertyName: 'transform' }))

    expect(done).toHaveBeenCalled()
    expect(el.style.zIndex).toBe('') // released back to the CSS class's own z-index
  })

  it('onChatEnter ignores a transitionend for a different property', async () => {
    const s = mount()
    const el = document.createElement('div')
    const done = vi.fn()

    s.onChatEnter(el, done)
    await vi.advanceTimersByTimeAsync(250)
    flushRaf()

    el.dispatchEvent(new TransitionEvent('transitionend', { propertyName: 'opacity' }))

    expect(done).not.toHaveBeenCalled()
  })

  describe('onChatBeforeLeave / onChatLeave direction dependence', () => {
    it('a forward leave (further push while chat showed) stays at the base z-index and exits the opposite way', async () => {
      navDirection.value = 'forward'
      const s = mount()
      const el = document.createElement('div')

      s.onChatBeforeLeave(el)
      expect(el.style.zIndex).toBe('')
      expect(el.style.transform).toBe('rotateY(0deg)')

      s.onChatLeave(el, () => {})
      expect(el.style.transition).toBe('transform 250ms ease-in-out')
      flushRaf()
      expect(el.style.transform).toBe('rotateY(90deg)')
    })

    it('a back leave (pop to Manage projects) raises z-index and exits the other way, eased in', async () => {
      navDirection.value = 'back'
      const s = mount()
      const el = document.createElement('div')

      s.onChatBeforeLeave(el)
      expect(el.style.zIndex).toBe('101')

      s.onChatLeave(el, () => {})
      expect(el.style.transition).toBe('transform 250ms ease-in')
      flushRaf()
      expect(el.style.transform).toBe('rotateY(-90deg)')
    })
  })
})
