// The input no longer closes while the model is answering: anything the
// user sends meanwhile is answered by the next turn, together with
// whatever else is waiting (see the backend's own coalescing). The only
// things that still close it are an unusable session and a chat socket
// that is not connected.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
vi.mock('../src/mic.js', () => ({ startRecording: vi.fn(), stopRecording: vi.fn() }))
vi.mock('../src/audio.js', () => ({
  playMessageChime: vi.fn(), playMessageAudio: vi.fn(), playReactionChime: vi.fn(), unlockAudioPlayback: vi.fn(),
}))
vi.mock('../src/chatClient.js', () => ({
  sendMessage: vi.fn(),
  getConnectionState: vi.fn(() => 'open'),
  onConnectionState: vi.fn(() => () => {}),
  resolvePendingTurnsAfterReload: vi.fn(),
}))
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
  projectFileContentUrl: vi.fn(() => '/skin.css'),
}))

describe('ChatView keeps the input open while a reply is being generated', () => {
  let chatStore
  let chatClient
  let api
  let container

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    chatClient = await import('../src/chatClient.js')
    api = await import('../src/api.js')
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    container.remove()
  })

  it('leaves the text input enabled with a turn in flight, and takes a second message', async () => {
    api.getCurrentSession.mockResolvedValue({
      id: 1, active: true, state: { key: 'x', ui_label: 'X', actions: [], chat: true },
    })
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await chatStore.loadMessages()

    chatClient.sendMessage.mockImplementation(() => new Promise(() => {}))
    chatStore.handleSend('I have a problem')
    await vi.waitFor(() => expect(chatStore.chatLoading.value).toBe(true))

    const input = container.querySelector('textarea, input[type="text"]')
    expect(input).not.toBeNull()
    expect(input.disabled).toBe(false)

    chatStore.handleSend('with flight VY3003')
    await vi.waitFor(() => {
      expect(chatStore.messages.value.filter((m) => m.role === 'user')).toHaveLength(2)
    })
    expect(chatClient.sendMessage).toHaveBeenCalledTimes(2)

    app.unmount()
  })
})
