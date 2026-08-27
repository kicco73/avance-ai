import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn(), connect: vi.fn(), disconnect: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn() }))
vi.mock('../src/mic.js', () => ({ startRecording: vi.fn(), stopRecording: vi.fn() }))
vi.mock('../src/audio.js', () => ({ playMessageChime: vi.fn(), playMessageAudio: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getCurrentSession: vi.fn(),
  postCreateSession: vi.fn(),
  getCurrentTestSession: vi.fn(),
  postCreateTestSession: vi.fn(),
  getSessions: vi.fn().mockResolvedValue([]),
  getTestSessions: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn(),
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
  projectFileContentUrl: vi.fn((p, f, s) => `/api/projects/${p}/files/${f}/content?session_id=${s}`)
}))

describe("ChatView.vue's sessions panel auto-collapses after 5s idle", () => {
  let chatStore
  let container

  beforeEach(async () => {
    vi.resetModules()
    vi.useFakeTimers()
    chatStore = await import('../src/chatStore.js')
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
    container.remove()
  })

  it('collapses on its own after 5s with no interaction', async () => {
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await nextTick()

    chatStore.sessionsPanelOpen.value = true
    await nextTick()

    await vi.advanceTimersByTimeAsync(5000)

    expect(chatStore.sessionsPanelOpen.value).toBe(false)

    app.unmount()
  })

  it('stays open when an interaction resets the timer before 5s elapse', async () => {
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: false })
    app.mount(container)
    await nextTick()

    chatStore.sessionsPanelOpen.value = true
    await nextTick()

    await vi.advanceTimersByTimeAsync(3000)
    container.querySelector('.sessions-panel-wrap').dispatchEvent(new Event('mousemove', { bubbles: true }))
    await nextTick()
    await vi.advanceTimersByTimeAsync(3000)

    expect(chatStore.sessionsPanelOpen.value).toBe(true)

    await vi.advanceTimersByTimeAsync(2000)
    expect(chatStore.sessionsPanelOpen.value).toBe(false)

    app.unmount()
  })

  it('never auto-collapses an instance whose panel is hidden entirely', async () => {
    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: true, themeMode: 'manual' })
    app.mount(container)
    await nextTick()

    chatStore.sessionsPanelOpen.value = true
    await nextTick()

    await vi.advanceTimersByTimeAsync(10000)

    expect(chatStore.sessionsPanelOpen.value).toBe(true)

    app.unmount()
  })
})
