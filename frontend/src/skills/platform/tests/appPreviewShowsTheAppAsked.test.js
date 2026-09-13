// Picking another app while the first one's transcript was still in
// flight let the late answer win: the card ended up showing a
// conversation belonging to an app nobody had selected — and only
// sometimes, since it depends on which request answers last.
//
// The same stale-response guard chatSkin.js's loadSkin has always had.
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import { resetFakeBus } from '../../../../tests/fakeBus.js'

vi.mock('../api.js', () => ({ getAppPreviewTranscript: vi.fn() }))
vi.mock('../../../busChannel.js', () => import('../../../../tests/fakeBus.js'))

// The card's whole component tree is transformed here, at import time,
// rather than in the hook below: that cost is 5.9s on its own and 16.2s
// with the whole suite running in parallel, and vitest charged it to the
// hook's own 10s budget. A file's own imports are not timed, so the
// import in the hook is left with nothing but the re-evaluation.
await import('../components/appStore/AppStoreFrozenPreview.vue')

describe('an app card', () => {
  let container
  let api
  let AppStoreFrozenPreview

  beforeEach(async () => {
    vi.resetModules()
    // Each test scripts the transcript request itself, and one that never
    // answers must not be what the next one gets. The fake socket is
    // shared across files in a worker, so it is cleared here too.
    vi.resetAllMocks()
    resetFakeBus()
    api = await import('../api.js')
    AppStoreFrozenPreview = (await import('../components/appStore/AppStoreFrozenPreview.vue')).default
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    container.remove()
  })

  function transcriptOf(appId) {
    return { messages: [{ id: `${appId}-1`, role: 'assistant', content: `I am ${appId}.`, timestamp: 't' }] }
  }

  it('shows the app it was asked about, not the one that answered last', async () => {
    // The first app's answer is held back until after the second's has
    // landed — the order that used to put the wrong app on screen.
    let answerFirst
    api.getAppPreviewTranscript.mockImplementation((appId) => (
      appId === 'first'
        ? new Promise((resolve) => { answerFirst = () => resolve(transcriptOf('first')) })
        : Promise.resolve(transcriptOf('second'))
    ))

    const app = createApp({
      data: () => ({ appId: 'first' }),
      components: { AppStoreFrozenPreview },
      template: '<AppStoreFrozenPreview :app-id="appId" />',
    })
    const mounted = app.mount(container)

    mounted.appId = 'second'
    await nextTick()
    await Promise.resolve()
    answerFirst()
    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()

    expect(container.textContent).toContain('I am second.')
    expect(container.textContent).not.toContain('I am first.')

    app.unmount()
  })

  it('waits on the same panel every other chat waits on, rather than showing the app before', async () => {
    let answer
    api.getAppPreviewTranscript.mockImplementation(() => new Promise((resolve) => { answer = resolve }))

    const app = createApp({
      data: () => ({ appId: 'slow' }),
      components: { AppStoreFrozenPreview },
      template: '<AppStoreFrozenPreview :app-id="appId" />',
    })
    app.mount(container)
    await nextTick()

    expect(container.querySelector('.chat-waiting-panel')).not.toBeNull()

    answer(transcriptOf('slow'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()

    expect(container.querySelector('.chat-waiting-panel')).toBeNull()
    expect(container.textContent).toContain('I am slow.')

    app.unmount()
  })
})
