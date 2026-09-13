// Regression: ChatView.vue's onVisibilityChange used to call
// reloadMessages() unconditionally on returning to the tab — including
// while a reply was being written, replacing `messages` out from under
// the bubble it was being written into. It now skips the reload while
// chatLoading is true, and the exchange finishes that bubble itself (see
// chatStoreFactory.js's watchReply).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
vi.mock('../src/audio.js', () => ({ playMessageChime: vi.fn(), playReactionChime: vi.fn(), unlockAudioPlayback: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getSessions: vi.fn().mockResolvedValue([]),
  getHistory: vi.fn().mockResolvedValue([]),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  deleteSession: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  postTruncateSession: vi.fn(),
  getProjects: vi.fn().mockResolvedValue({ projects: [{ id: 'proj', ui_label: 'Proj' }], active: 'proj' }),
  projectFileContentUrl: vi.fn(() => '/skin.css')
}))

// Mounting ChatView is the heaviest thing this suite does, and the whole
// component tree is transformed here, at import time, rather than inside
// whichever test imports it first: that cost is 6.3s on its own and
// 16.3s with the whole suite running in parallel, and vitest charged it
// to that test's own 5s budget. A file's own imports are not timed, so
// the import below is left with nothing but the re-evaluation.
await import('../src/components/chat/ChatView.vue')

describe('ChatView.vue never reloads messages mid-turn on visibilitychange', () => {
  let bus
  let deliver
  let chatStore
  let api
  let container

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    chatStore = await import('../src/chatStore.js')
    api = await import('../src/api.js')
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  })

  it('skips reloadMessages while chatLoading is true, and calls it once the turn resolves', async () => {
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await chatStore.loadMessages('proj')
    bus.deliverEntered({ sessionId: 1, projectId: 'proj', state: { key: 'x', ui_label: 'X', actions: [] } })
    api.getHistory.mockClear()

    await chatStore.handleSend('hi')
    // The reply has started being written — which is what chatLoading
    // means now: not "a message was sent", but "something is arriving"
    // (see chatStoreFactory.js's watchReply).
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    expect(chatStore.chatLoading.value).toBe(true)

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(api.getHistory).not.toHaveBeenCalled()

    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 5, text: 'done', timestamp: 't' })
    expect(chatStore.chatLoading.value).toBe(false)

    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(api.getHistory).toHaveBeenCalledTimes(1)

    app.unmount()
  })
})
