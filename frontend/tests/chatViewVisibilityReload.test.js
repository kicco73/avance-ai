// Regression: ChatView.vue's onVisibilityChange used to call
// reloadMessages() unconditionally on returning to the tab — including
// while a reply was being written, replacing `messages` out from under
// the bubble it was being written into. It now skips the reload while
// chatLoading is true, and the exchange finishes that bubble itself (see
// chatStoreFactory.js's watchReply).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installApiBackedLiveChannel } from './liveChatChannelStub.js'
import { createApp } from 'vue'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
vi.mock('../src/audio.js', () => ({ playMessageChime: vi.fn(), playReactionChime: vi.fn(), unlockAudioPlayback: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getCurrentSession: vi.fn(),
  postCreateSession: vi.fn(),
  postCloseSession: vi.fn(),
  getCurrentTestSession: vi.fn(),
  postCreateTestSession: vi.fn(),
  getSessions: vi.fn(),
  getTestSessions: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn().mockResolvedValue([]),
  getSessionState: vi.fn(),
  postAction: vi.fn(),
  getAutoTracking: vi.fn(),
  putAutoTracking: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  putMessageReaction: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn(),
  getProjects: vi.fn().mockResolvedValue({ projects: [{ id: 'proj', ui_label: 'Proj' }], active: 'proj' }),
  projectFileContentUrl: vi.fn(() => '/skin.css')
}))

// Mounting ChatView is the heaviest thing this suite does, and vitest's
// 5s default is measured against an idle machine. This file alone takes
// about 8s; under the whole suite's parallel load it lost to a 10s ceiling,
// so the limit is that observed ceiling plus 30%.
vi.setConfig({ testTimeout: 13_000 })

describe('ChatView.vue never reloads messages mid-turn on visibilitychange', () => {
  let deliver
  let chatStore
  let api
  let container

  beforeEach(async () => {
    vi.resetModules()
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    chatStore = await import('../src/chatStore.js')
    api = await import('../src/api.js')
    await installApiBackedLiveChannel(api)
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
  })

  it('skips reloadMessages while chatLoading is true, and calls it once the turn resolves', async () => {
    api.getCurrentSession.mockResolvedValue({ id: 1, current: true, state: { key: 'x', ui_label: 'X', actions: [] } })
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await chatStore.loadMessages()
    api.getMessages.mockClear()

    await chatStore.handleSend('hi')
    // The reply has started being written — which is what chatLoading
    // means now: not "a message was sent", but "something is arriving"
    // (see chatStoreFactory.js's watchReply).
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    expect(chatStore.chatLoading.value).toBe(true)

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(api.getMessages).not.toHaveBeenCalled()

    deliver({ type: 'ui.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 5, text: 'done', timestamp: 't' })
    expect(chatStore.chatLoading.value).toBe(false)

    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(api.getMessages).toHaveBeenCalledTimes(1)

    app.unmount()
  })
})
