import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import { resetFakeBus } from '../../../../tests/fakeBus.js'

vi.mock('../api.js', () => ({ getAppPreviewTranscript: vi.fn() }))
vi.mock('../../../busChannel.js', () => import('../../../../tests/fakeBus.js'))

await import('../components/appStore/AppStoreFrozenPreview.vue')

describe('an app card', () => {
  let container
  let api
  let AppStoreFrozenPreview

  beforeEach(async () => {
    vi.resetModules()
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
