// Regression coverage for chatStoreFactory.js's createChatStore: the live
// chat (chatStore.js) and EditProjectView.vue's own embedded "Run" test
// chat (testChatStore.js) are two independent instances, each always
// bound to its own endpoints (getCurrentSession/postCreateSession/
// getSessions vs getCurrentTestSession/postCreateTestSession/
// getTestSessions/postResetTestSessions) — never a shared flag deciding
// which pool a single store instance happens to be routed to right now.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getCurrentSession: vi.fn(),
  postCreateSession: vi.fn(),
  getCurrentTestSession: vi.fn(),
  postCreateTestSession: vi.fn(),
  getSessions: vi.fn(),
  getTestSessions: vi.fn(),
  deleteSession: vi.fn(),
  postCloseSession: vi.fn(),
  getMessages: vi.fn(),
  postAction: vi.fn(),
  getAutoTracking: vi.fn(),
  postAutoTracking: vi.fn(),
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  messageAudioUrl: vi.fn(),
  postListenTranscribe: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn(),
  projectFileContentUrl: vi.fn(() => '/skin.css')
}))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn(), getConnectionState: vi.fn(() => 'open'), onConnectionState: vi.fn(() => () => {}), resolvePendingTurnsAfterReload: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

const STATE = { key: 'x', ui_label: 'X', actions: [] }

describe('the live store and the test store always route to their own endpoints', () => {
  let chatStore
  let testChatStore
  let api
  let dialogStore
  let onEnterActions

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    testChatStore = await import('../src/testChatStore.js')
    testChatStore.setTestProject('my-project')
    api = await import('../src/api.js')
    dialogStore = await import('../src/dialogStore.js')
    onEnterActions = await import('../src/onEnterActions.js')
    api.getMessages.mockResolvedValue([])
    api.getSessions.mockResolvedValue([])
    api.getTestSessions.mockResolvedValue([])
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('loadMessages and loadSessions each hit their own pool, never the other one', async () => {
    api.getCurrentSession.mockResolvedValue({ id: 1, active: true, state: STATE })
    api.getCurrentTestSession.mockResolvedValue({ id: 2, active: true, state: STATE })

    await chatStore.loadMessages()
    await chatStore.loadSessions()
    expect(api.getCurrentSession).toHaveBeenCalledWith(null)
    expect(api.getSessions).toHaveBeenCalled()
    expect(api.getCurrentTestSession).not.toHaveBeenCalled()
    expect(api.getTestSessions).not.toHaveBeenCalled()

    await testChatStore.loadMessages()
    await testChatStore.loadSessions()
    expect(api.getCurrentTestSession).toHaveBeenCalledWith(null, 'my-project')
    expect(api.getTestSessions).toHaveBeenCalledWith('my-project')
  })

  it('handleNewSession creates in its own pool — the live one confirming first, the test one scoped to its project and unconfirmed', async () => {
    api.postCreateSession.mockResolvedValue({ id: 3, active: true })
    api.getCurrentSession.mockResolvedValue({ id: 3, active: true, state: STATE })

    await chatStore.handleNewSession()

    expect(dialogStore.confirmDialog).toHaveBeenCalled()
    expect(api.postCreateSession).toHaveBeenCalled()
    expect(api.postCreateTestSession).not.toHaveBeenCalled()
    // init-action's own on-enter arrives over the websocket, never off this response.
    expect(onEnterActions.runOnEnterScript).not.toHaveBeenCalled()

    dialogStore.confirmDialog.mockClear()
    api.postCreateTestSession.mockResolvedValue({ id: 4, active: true })
    api.getCurrentTestSession.mockResolvedValue({ id: 4, active: true, state: STATE })

    await testChatStore.handleNewSession()

    expect(dialogStore.confirmDialog).not.toHaveBeenCalled()
    expect(api.postCreateTestSession).toHaveBeenCalledWith('my-project')
  })

  it('only the test store can reset, applying the returned state as-is, and the live store has no handleReset at all', async () => {
    expect(chatStore.liveStore.handleReset).toBeNull()

    api.postResetTestSessions.mockResolvedValue({ key: 'a', ui_label: 'A', actions: [] })
    api.getCurrentTestSession.mockResolvedValue({ id: 6, active: true, state: { key: 'a', ui_label: 'A', actions: [] } })

    await testChatStore.handleReset()

    expect(api.postResetTestSessions).toHaveBeenCalledWith('my-project')
    // Its on-enter arrives over the websocket too.
    expect(onEnterActions.runOnEnterScript).not.toHaveBeenCalled()
    expect(testChatStore.state.value).toEqual({ key: 'a', ui_label: 'A', actions: [] })
  })

  it('handleCloseSession closes the current session unconfirmed and reflects it back, and is a no-op with no session yet', async () => {
    await chatStore.handleCloseSession()
    expect(api.postCloseSession).not.toHaveBeenCalled()

    api.getCurrentSession.mockResolvedValue({ id: 7, active: true, state: STATE })
    await chatStore.loadMessages()
    api.postCloseSession.mockResolvedValue({ id: 7, active: false })

    await chatStore.handleCloseSession()

    expect(dialogStore.confirmDialog).not.toHaveBeenCalled()
    expect(api.postCloseSession).toHaveBeenCalledWith(7)
    expect(chatStore.selectedSessionActive.value).toBe(false)
  })
})
