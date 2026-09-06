// Regression: ChatView.vue's own v-for used to key a message bubble by
// `msg.messageId || msg.id || i` — a live turn's placeholder starts with
// messageId: null (key falls back to msg.id), then chatStoreFactory.js
// backfills the real backend messageId once the turn completes, flipping
// the v-for key and forcing Vue to unmount/remount the whole MessageBubble
// at exactly the moment its final content (and, for a tool-call turn, its
// trace) land — the visible "glitch" a streamed reply had that a reload
// (whose messages arrive with messageId already stable) never did. The
// fix keys on msg.id first, which never changes across a message's own
// lifetime, so the bubble instance survives the messageId backfill intact.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
vi.mock('../src/mic.js', () => ({ startRecording: vi.fn(), stopRecording: vi.fn() }))
vi.mock('../src/audio.js', () => ({ playMessageChime: vi.fn(), playMessageAudio: vi.fn() }))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn(), connect: vi.fn(), disconnect: vi.fn(), getConnectionState: vi.fn(() => 'open'), onConnectionState: vi.fn(() => () => {}), resolvePendingTurnsAfterReload: vi.fn() }))
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

describe("a live turn's assistant bubble survives the messageId backfill without remounting", () => {
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
  })

  it('keeps the same .bubble-assistant DOM node from first chunk through assistant_message_id landing', async () => {
    api.getCurrentSession.mockResolvedValue({ id: 1, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await chatStore.loadMessages()
    await vi.waitFor(() => expect(container.querySelector('.projects-btn')).not.toBeNull())

    let resolveTurn
    chatClient.sendMessage.mockImplementation((_text, _sessionId, { onChunk }) => {
      onChunk('Hello')
      return new Promise((resolve) => { resolveTurn = resolve })
    })

    const sendPromise = chatStore.handleSend('hi')
    await vi.waitFor(() => expect(container.querySelector('.bubble-assistant')).not.toBeNull())

    const bubbleBefore = container.querySelector('.bubble-assistant')
    expect(bubbleBefore.textContent).toContain('Hello')

    resolveTurn({
      reply: [], user_message_id: 10, assistant_message_id: 99,
      state: { key: 'x', ui_label: 'X', actions: [] }, 'on-enter': null, session_id: 1,
    })
    await sendPromise
    await new Promise((resolve) => setTimeout(resolve, 0))

    const bubbleAfter = container.querySelector('.bubble-assistant')
    expect(bubbleAfter).toBe(bubbleBefore)
    expect(bubbleAfter.textContent).toContain('Hello')

    app.unmount()
  })
})
