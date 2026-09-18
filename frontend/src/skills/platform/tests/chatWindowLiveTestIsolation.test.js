import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h } from 'vue'
import { resetFakeBus } from '../../../../tests/fakeBus.js'

vi.mock('../../../busChannel.js', () => import('../../../../tests/fakeBus.js'))
vi.mock('../../../taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../../../dialogStore.js', () => ({ confirmDialog: vi.fn() }))
vi.mock('../../../audio.js', () => ({ playMessageChime: vi.fn(), playReactionChime: vi.fn(), unlockAudioPlayback: vi.fn() }))
vi.mock('../../../api.js', () => ({
  getSessions: vi.fn(),
  getTestSessions: vi.fn(),
  deleteSession: vi.fn(),
  getHistory: vi.fn(),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn(),
  getTestChatModels: vi.fn(),
  postTestChatModelSelection: vi.fn(),
  getProjects: vi.fn().mockResolvedValue({ projects: [], active: null }),
  getSubscribedProjects: vi.fn().mockResolvedValue({ projects: [], active: null }),
  projectFileContentUrl: vi.fn((p, f, s) => `/api/core/projects/${p}/files/${f}/content?session_id=${s}`)
}))

await import('../../../components/chat/ChatView.vue')

describe('the live chat and the "Run" test chat are genuinely independent stores', () => {
  beforeEach(resetFakeBus)

  it('each ChatView instance shows only its own store\'s content, simultaneously, with no clearing needed', async () => {
    const chatStore = await import('../../../chatStore.js')
    const testChatStore = await import('../testChatStore.js')
    const ChatWindow = (await import('../../../components/chat/ChatView.vue')).default

    chatStore.state.value = { key: 'live-state', ui_label: 'Live', actions: [] }
    chatStore.currentSessionId.value = 7
    chatStore.selectedSessionActive.value = true
    chatStore.messages.value = [
      { id: 1, role: 'assistant', content: 'LIVE-MODE-CONTENT', timestamp: new Date().toISOString() }
    ]

    testChatStore.state.value = { key: 'test-state', ui_label: 'Test', actions: [] }
    testChatStore.currentSessionId.value = 42
    testChatStore.selectedSessionActive.value = true
    testChatStore.messages.value = [
      { id: 1, role: 'assistant', content: 'TEST-MODE-CONTENT', timestamp: new Date().toISOString() }
    ]

    const liveContainer = document.createElement('div')
    document.body.appendChild(liveContainer)
    const liveApp = createApp({ render: () => h(ChatWindow, { hideSessionsPanel: false }) })
    liveApp.mount(liveContainer)

    const testContainer = document.createElement('div')
    document.body.appendChild(testContainer)
    const testApp = createApp({
      render: () => h(ChatWindow, { hideSessionsPanel: true, themeMode: 'manual', store: testChatStore.testStore })
    })
    testApp.mount(testContainer)

    expect(liveContainer.textContent).toContain('LIVE-MODE-CONTENT')
    expect(liveContainer.textContent).not.toContain('TEST-MODE-CONTENT')
    expect(testContainer.textContent).toContain('TEST-MODE-CONTENT')
    expect(testContainer.textContent).not.toContain('LIVE-MODE-CONTENT')

    testApp.unmount()
    testContainer.remove()
    expect(liveContainer.textContent).toContain('LIVE-MODE-CONTENT')
    expect(chatStore.messages.value).toHaveLength(1)

    liveApp.unmount()
    liveContainer.remove()
  })

  it("browsing an imported session's id in one store's currentSessionId never touches the other's", async () => {
    const chatStore = await import('../../../chatStore.js')
    const testChatStore = await import('../testChatStore.js')

    chatStore.currentSessionId.value = 7
    testChatStore.currentSessionId.value = 123

    expect(chatStore.currentSessionId.value).toBe(7)
    expect(testChatStore.currentSessionId.value).toBe(123)
  })
})
