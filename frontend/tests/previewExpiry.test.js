// The countdown that ends a try-it-out chat session (see
// usePreviewExpiry.js). It is what the app store's "Try me!" and Manage
// projects' own "Test" both close their preview on — Test had lost it
// entirely and ran until the panel was closed by hand.
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import { usePreviewExpiry } from '../src/composables/usePreviewExpiry.js'

const EXPIRY_SECONDS = 5 * 60

// usePreviewExpiry registers onBeforeUnmount, so it has to run inside a
// real component instance rather than bare.
function mountWithExpiry() {
  let api
  const app = createApp({
    setup() {
      api = usePreviewExpiry()
      return () => null
    }
  })
  app.mount(document.createElement('div'))
  return { api, unmount: () => app.unmount() }
}

describe('usePreviewExpiry', () => {
  let mounted

  beforeEach(() => {
    vi.useFakeTimers()
    mounted = mountWithExpiry()
  })

  afterEach(() => {
    mounted.unmount()
    vi.useRealTimers()
  })

  it('does not expire until armed, however long goes by', () => {
    vi.advanceTimersByTime(EXPIRY_SECONDS * 2 * 1000)

    expect(mounted.api.expired.value).toBe(false)
  })

  it('expires once the full window has gone by', () => {
    mounted.api.arm()

    vi.advanceTimersByTime((EXPIRY_SECONDS - 1) * 1000)
    expect(mounted.api.expired.value).toBe(false)

    vi.advanceTimersByTime(1000)
    expect(mounted.api.expired.value).toBe(true)
  })

  it('gives a restarted preview the whole window again, not the remainder', () => {
    mounted.api.arm()
    vi.advanceTimersByTime((EXPIRY_SECONDS - 1) * 1000)

    mounted.api.arm()
    vi.advanceTimersByTime((EXPIRY_SECONDS - 1) * 1000)

    expect(mounted.api.expired.value).toBe(false)
    vi.advanceTimersByTime(1000)
    expect(mounted.api.expired.value).toBe(true)
  })

  it('never expires after being cleared', () => {
    mounted.api.arm()
    vi.advanceTimersByTime(1000)
    mounted.api.clear()

    vi.advanceTimersByTime(EXPIRY_SECONDS * 2 * 1000)

    expect(mounted.api.expired.value).toBe(false)
  })

  it('shows the Quit button as a countdown only in the last minute', async () => {
    expect(mounted.api.quitButtonLabel.value).toBe('Quit')

    mounted.api.arm()
    vi.advanceTimersByTime((EXPIRY_SECONDS - 60) * 1000)
    await nextTick()
    expect(mounted.api.quitButtonLabel.value).toBe('Quit')

    vi.advanceTimersByTime(1000)
    await nextTick()
    expect(mounted.api.quitButtonLabel.value).toBe('0:59')
  })
})
