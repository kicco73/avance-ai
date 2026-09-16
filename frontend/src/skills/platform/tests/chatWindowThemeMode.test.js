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
  projectFileContentUrl: vi.fn((projectName, fileName, sessionId) => `/api/core/projects/${projectName}/files/${fileName}/content?session_id=${sessionId}`)
}))

function currentSkinStyleTags() {
  return Array.from(document.head.querySelectorAll('style'))
}

await import('../../../components/chat/ChatView.vue')

describe('ChatView.vue themeMode="manual" end to end (not just the store refs)', () => {
  let chatStore
  let fetchMock
  let container

  beforeEach(async () => {
    resetFakeBus()
    vi.resetModules()
    document.head.innerHTML = ''
    chatStore = await import('../../../chatStore.js')
    fetchMock = vi.fn().mockResolvedValue({ ok: true, text: async () => '.chat-window-shell { color: red; }' })
    global.fetch = fetchMock
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    vi.clearAllMocks()
    document.head.innerHTML = ''
    container.remove()
  })

  it('mounting ChatWindow with theme-mode="manual" clears the shared skin tag', async () => {
    chatStore.currentProjectId.value = 'proj'
    chatStore.currentSessionId.value = 1
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))

    const ChatWindow = (await import('../../../components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: true, themeMode: 'manual' })
    app.mount(container)
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(0))

    app.unmount()
  })

  it('an always-mounted auto ChatWindow (App.vue) plus a manual one entering (RunChat) — Run mode opening over the live chat', async () => {
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

    chatSkin.activeChatMode.value = 'test'
    const testContainer = document.createElement('div')
    document.body.appendChild(testContainer)
    const testApp = createApp(ChatWindow, { hideSessionsPanel: true, themeMode: 'manual', store: testChatStore.testStore })
    testApp.mount(testContainer)
    await nextTick()
    await testChatStore.loadMessages()
    deliverEntered({
      sessionId: 99, projectId: 'test-proj', sessionType: 'test',
      state: { key: 'test', ui_label: 'Test', actions: [] },
    })

    await nextTick()
    expect(currentSkinStyleTags()).toHaveLength(0)

    liveApp.unmount()
    testApp.unmount()
  })

  it('a fetch already in flight when applyAspect flips off must not re-apply the skin once it lands', async () => {
    let resolveFetch
    fetchMock.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve }))

    chatStore.currentProjectId.value = 'proj'
    chatStore.currentSessionId.value = 1
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    chatStore.applyAspect.value = false

    resolveFetch({ ok: true, text: async () => 'body { color: red; }' })
    await new Promise((r) => setTimeout(r, 0))
    await new Promise((r) => setTimeout(r, 0))

    expect(currentSkinStyleTags()).toHaveLength(0)
  })
})
