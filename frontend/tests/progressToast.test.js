import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, ref } from 'vue'

import ProgressToast from '../src/components/chat/ProgressToast.vue'

describe('the progress toast', () => {
  let container
  let app
  let progress

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame'] })
    container = document.createElement('div')
    document.body.appendChild(container)
    progress = ref(null)
    app = createApp({
      components: { ProgressToast },
      setup: () => ({ progress }),
      template: '<ProgressToast :progress="progress" />'
    })
    app.mount(container)
  })

  afterEach(() => {
    app.unmount()
    container.remove()
    vi.useRealTimers()
  })

  async function show(title, percentage) {
    progress.value = { title, percentage }
    await nextTick()
  }

  function fill() {
    return container.querySelector('.progress-toast-fill')
  }

  it('shows the title', async () => {
    await show('Closing in 3 turns', 40)

    expect(container.querySelector('.progress-toast-title').textContent).toBe('Closing in 3 turns')
  })

  it('grows the bar from the previous value to the new one', async () => {
    await show('Step', 40)
    await vi.advanceTimersToNextFrame()
    await vi.advanceTimersToNextFrame()
    await nextTick()

    await show('Step', 70)
    expect(fill().style.width).toBe('40%')

    await vi.advanceTimersToNextFrame()
    await vi.advanceTimersToNextFrame()
    await nextTick()
    expect(fill().style.width).toBe('70%')
  })

  it('goes away five seconds after the last progress', async () => {
    await show('Step', 40)

    vi.advanceTimersByTime(4999)
    await nextTick()
    expect(container.querySelector('.progress-toast')).not.toBe(null)

    vi.advanceTimersByTime(1)
    await nextTick()
    await vi.runAllTimersAsync()
    expect(container.querySelector('.progress-toast')).toBe(null)
  })
})
