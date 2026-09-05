// Regression: ChatView.vue's onVisibilityChange used to call
// reloadMessages() unconditionally on returning to the tab — including
// mid-turn, replacing `messages` out from under the bubble submitMessage
// is still streaming into. It now skips the reload while chatLoading is
// true; the in-flight turn's own `done` handler reconciles that bubble
// itself once it lands (see chatStoreFactory.js's submitMessage).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
vi.mock('../src/mic.js', () => ({ startRecording: vi.fn(), stopRecording: vi.fn() }))
vi.mock('../src/audio.js', () => ({ playMessageChime: vi.fn(), playMessageAudio: vi.fn() }))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn(), connect: vi.fn(), disconnect: vi.fn() }))
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
  postAutoTracking: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  putMessageReaction: vi.fn(),
  messageAudioUrl: vi.fn(),
  postListenTranscribe: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn(),
  getProjects: vi.fn().mockResolvedValue({ projects: [{ id: 'proj', ui_label: 'Proj' }], active: 'proj' }),
  projectFileContentUrl: vi.fn(() => '/skin.css')
}))

describe('ChatView.vue never reloads messages mid-turn on visibilitychange', () => {
  let chatClient
  let chatStore
  let api
  let container

  beforeEach(async () => {
    vi.resetModules()
    chatClient = await import('../src/chatClient.js')
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
    api.getCurrentSession.mockResolvedValue({ id: 1, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await chatStore.loadMessages()
    api.getMessages.mockClear()

    let resolveTurn
    chatClient.sendMessage.mockImplementation(() => new Promise((resolve) => { resolveTurn = resolve }))

    const sendPromise = chatStore.handleSend('hi')
    await vi.waitFor(() => expect(chatStore.chatLoading.value).toBe(true))

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(api.getMessages).not.toHaveBeenCalled()

    resolveTurn({
      reply: [{ id: 5, content: 'done', audio_text: null, timestamp: 't' }],
      user_message_id: 1, assistant_message_id: 5,
      state: { key: 'x', ui_label: 'X', actions: [] }, 'on-enter': null, session_id: 1,
    })
    await sendPromise

    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(api.getMessages).toHaveBeenCalledTimes(1)

    app.unmount()
  })
})
