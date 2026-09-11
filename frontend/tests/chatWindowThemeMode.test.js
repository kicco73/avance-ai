// Mounts the REAL ChatView.vue (not just chatStore.js's bare refs, unlike
// chatStoreSkin.test.js) to check the theme-mode prop end to end, including
// the async race a bare-refs test can't reach.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installApiBackedLiveChannel } from './liveChatChannelStub.js'
import { createApp, nextTick } from 'vue'

vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn(), connect: vi.fn(), disconnect: vi.fn(), getConnectionState: vi.fn(() => 'open'), onConnectionState: vi.fn(() => () => {}), resolvePendingTurnsAfterReload: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn() }))
vi.mock('../src/audio.js', () => ({ playMessageChime: vi.fn(), playReactionChime: vi.fn(), unlockAudioPlayback: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getCurrentSession: vi.fn(),
  postCreateSession: vi.fn(),
  getCurrentTestSession: vi.fn(),
  postCreateTestSession: vi.fn(),
  getSessions: vi.fn(),
  getTestSessions: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn(),
  getSessionState: vi.fn(),
  postAction: vi.fn(),
  getAutoTracking: vi.fn(),
  putAutoTracking: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  putMessageReaction: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn(),
  getTestChatModels: vi.fn(),
  postTestChatModelSelection: vi.fn(),
  projectFileContentUrl: vi.fn((projectName, fileName, sessionId) => `/api/skills/platform/projects/${projectName}/files/${fileName}/content?session_id=${sessionId}`)
}))

function currentSkinStyleTags() {
  return Array.from(document.head.querySelectorAll('style'))
}

// Mounting ChatView is the heaviest thing this suite does, and vitest's
// 5s default is measured against an idle machine: with another suite
// running alongside these time out while passing in about a second on
// their own.
vi.setConfig({ testTimeout: 10_000 })

describe('ChatView.vue themeMode="manual" end to end (not just the store refs)', () => {
  let chatStore
  let fetchMock
  let container

  beforeEach(async () => {
    vi.resetModules()
    document.head.innerHTML = ''
    chatStore = await import('../src/chatStore.js')
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

    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default
    const app = createApp(ChatWindow, { hideSessionsPanel: true, themeMode: 'manual' })
    app.mount(container)
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(0))

    app.unmount()
  })

  it('an always-mounted auto ChatWindow (App.vue) plus a manual one entering (RunChat) — Run mode opening over the live chat', async () => {
    const api = await import('../src/api.js')
    await installApiBackedLiveChannel(api)
    const chatSkin = await import('../src/chatSkin.js')
    const testChatStore = await import('../src/testChatStore.js')
    testChatStore.setTestProject('test-proj')
    api.getCurrentSession.mockResolvedValue({ id: 1, project_id: 'live-proj', current: true, state: { key: 'live', ui_label: 'Live', actions: [] } })
    api.getCurrentTestSession.mockResolvedValue({ id: 99, project_id: 'test-proj', current: true, state: { key: 'test', ui_label: 'Test', actions: [] } })
    api.getMessages.mockResolvedValue([])

    const ChatWindow = (await import('../src/components/chat/ChatView.vue')).default

    const liveContainer = document.createElement('div')
    document.body.appendChild(liveContainer)
    const liveApp = createApp(ChatWindow, { hideSessionsPanel: false })
    liveApp.mount(liveContainer)
    await chatStore.loadMessages()
    await vi.waitFor(() => expect(currentSkinStyleTags()).toHaveLength(1))

    // EditProjectView's setMode('run'): flips activeChatMode, mounts
    // RunChat -> ChatWindow(manual, :store="testStore"), then calls
    // ensureDraftChatSession().
    chatSkin.activeChatMode.value = 'test'
    const testContainer = document.createElement('div')
    document.body.appendChild(testContainer)
    const testApp = createApp(ChatWindow, { hideSessionsPanel: true, themeMode: 'manual', store: testChatStore.testStore })
    testApp.mount(testContainer)
    await nextTick()
    await testChatStore.loadMessages()

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
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1)) // the fetch is now in flight

    chatStore.applyAspect.value = false // Test mode entered mid-fetch

    resolveFetch({ ok: true, text: async () => 'body { color: red; }' }) // the stale response lands
    await new Promise((r) => setTimeout(r, 0))
    await new Promise((r) => setTimeout(r, 0))

    expect(currentSkinStyleTags()).toHaveLength(0)
  })
})
