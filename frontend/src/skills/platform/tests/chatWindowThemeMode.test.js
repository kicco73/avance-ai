import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'
import { deliverEntered, resetFakeBus } from '../../../../tests/fakeBus.js'

vi.mock('../../../busChannel.js', () => import('../../../../tests/fakeBus.js'))
vi.mock('../../../taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../../../dialogStore.js', () => ({ confirmDialog: vi.fn() }))
vi.mock('../../../audio.js', () => ({ playMessageChime: vi.fn(), playReactionChime: vi.fn(), unlockAudioPlayback: vi.fn() }))
vi.mock('../../../api.js', () => ({
  getSessions: vi.fn().mockResolvedValue([]),
  getTestSessions: vi.fn().mockResolvedValue([]),
  deleteSession: vi.fn(),
  getHistory: vi.fn().mockResolvedValue([]),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn(),
  getTestChatModels: vi.fn(),
  postTestChatModelSelection: vi.fn(),
  getProjects: vi.fn().mockResolvedValue({ projects: [{ id: 'live-proj', ui_label: 'Live' }], active: 'live-proj' }),
  getSubscribedProjects: vi.fn().mockResolvedValue({ projects: [{ id: 'live-proj', ui_label: 'Live' }], active: 'live-proj' }),
  projectFileContentUrl: vi.fn((projectName, fileName, sessionId) => `/api/core/projects/${projectName}/files/${fileName}/content?session_id=${sessionId}`)
}))

function currentSkinStyleTags() {
  return Array.from(document.head.querySelectorAll('style'))
}

await import('../../../components/chat/ChatView.vue')

describe('ChatView.vue running two stores at once (live + test/run) — shared skin tag ownership', () => {
  let chatStore
  let fetchMock

  beforeEach(async () => {
    resetFakeBus()
    vi.resetModules()
    document.head.innerHTML = ''
    chatStore = await import('../../../chatStore.js')
    fetchMock = vi.fn().mockResolvedValue({ ok: true, text: async () => '.chat-window-shell { color: red; }' })
    global.fetch = fetchMock
  })

  afterEach(() => {
    vi.clearAllMocks()
    document.head.innerHTML = ''
  })

  it('an always-mounted auto ChatWindow (App.vue) plus a second one entering (RunChat) — Run mode opening over the live chat', async () => {
    const chatSkin = await import('../../../chatSkin.js')
    const testChatStore = await import('../testChatStore.js')
    testChatStore.setTestProject('test-proj')

    const ChatWindow = (await import('../../../components/chat/ChatView.vue')).default

    const liveContainer = document.createElement('div')
    document.body.appendChild(liveContainer)
    const liveApp = createApp(ChatWindow, { hideSessionsPanel: false })
    liveApp.mount(liveContainer)
    await chatStore.loadMessages('live-proj')
    deliverEntered({ sessionId: 1, projectId: 'live-proj', state: { key: 'live', ui_label: 'Live', actions: [] } })
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))
    expect(currentSkinStyleTags()[0].textContent).toContain('color: red')

    fetchMock.mockResolvedValue({ ok: true, text: async () => '.chat-window-shell { color: blue; }' })
    chatSkin.activeChatMode.value = 'test'
    const testContainer = document.createElement('div')
    document.body.appendChild(testContainer)
    const testApp = createApp(ChatWindow, { hideSessionsPanel: true, store: testChatStore.testStore })
    testApp.mount(testContainer)
    await nextTick()
    await testChatStore.loadMessages()
    deliverEntered({
      sessionId: 99, projectId: 'test-proj', sessionType: 'test',
      state: { key: 'test', ui_label: 'Test', actions: [] },
    })

    await vi.waitFor(() => expect(currentSkinStyleTags()[0]?.textContent).toContain('color: blue'))
    expect(currentSkinStyleTags()).toHaveLength(1)

    liveApp.unmount()
    testApp.unmount()
  })
})
