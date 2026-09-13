// Regression coverage for the actual reported bug: browsing/testing a
// session in one context (e.g. picking an imported session inside Label
// sessions, or EditProjectView's own "Run" test chat) must never leak
// into the live chat, and vice versa. The old design shared one set of
// refs (chatStore.js) between both, redirected by a testModeProjectName
// flag and patched up on unmount — fragile, and exactly what let this
// leak happen in the first place. Now the live chat (chatStore.js) and
// the "Run" test chat (testChatStore.js) are two fully independent
// createChatStore() instances, so this asserts they can carry totally
// different content *at the same time*, with no clearing/reset needed at all.
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
  getAutoTracking: vi.fn(),
  putAutoTracking: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn(),
  getTestChatModels: vi.fn(),
  postTestChatModelSelection: vi.fn(),
  getProjects: vi.fn().mockResolvedValue({ projects: [], active: null }),
  projectFileContentUrl: vi.fn((p, f, s) => `/api/core/projects/${p}/files/${f}/content?session_id=${s}`)
}))

// Mounting ChatView is the heaviest thing this suite does, and the whole
// component tree is transformed here, at import time, rather than inside
// whichever test imports it first: that cost is 6.3s on its own and
// 16.3s with the whole suite running in parallel, and vitest charged it
// to that test's own 5s budget. A file's own imports are not timed, so
// the import below is left with nothing but the re-evaluation.
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

    // Unmounting the Test one (leaving "Run" mode) must not disturb the
    // still-mounted live one at all — no shared clearChatUi() to race.
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
    testChatStore.currentSessionId.value = 123 // e.g. LabelProjectView.vue browsing an imported session

    expect(chatStore.currentSessionId.value).toBe(7)
    expect(testChatStore.currentSessionId.value).toBe(123)
  })
})
