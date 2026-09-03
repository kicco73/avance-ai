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
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn() }))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

describe('the live store and the test store always route to their own endpoints', () => {
  let chatStore
  let testChatStore
  let api

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    testChatStore = await import('../src/testChatStore.js')
    testChatStore.setTestProject('my-project')
    api = await import('../src/api.js')
    api.getMessages.mockResolvedValue([])
    api.getSessions.mockResolvedValue([])
    api.getTestSessions.mockResolvedValue([])
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it("the live store's loadMessages calls getCurrentSession, never getCurrentTestSession", async () => {
    api.getCurrentSession.mockResolvedValue({ id: 1, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })

    await chatStore.loadMessages()

    expect(api.getCurrentSession).toHaveBeenCalledWith(null)
    expect(api.getCurrentTestSession).not.toHaveBeenCalled()
  })

  it("the test store's loadMessages calls getCurrentTestSession, never getCurrentSession", async () => {
    api.getCurrentTestSession.mockResolvedValue({ id: 2, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })

    await testChatStore.loadMessages()

    expect(api.getCurrentTestSession).toHaveBeenCalledWith(null, 'my-project')
    expect(api.getCurrentSession).not.toHaveBeenCalled()
  })

  it("the live store's loadSessions calls getSessions, never getTestSessions", async () => {
    await chatStore.loadSessions()

    expect(api.getSessions).toHaveBeenCalled()
    expect(api.getTestSessions).not.toHaveBeenCalled()
  })

  it("the test store's loadSessions calls getTestSessions, never getSessions", async () => {
    await testChatStore.loadSessions()

    expect(api.getTestSessions).toHaveBeenCalledWith('my-project')
    expect(api.getSessions).not.toHaveBeenCalled()
  })

  it("the live store's handleNewSession calls postCreateSession, with confirmation", async () => {
    api.postCreateSession.mockResolvedValue({ id: 3, active: true })
    api.getCurrentSession.mockResolvedValue({ id: 3, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })
    const dialogStore = await import('../src/dialogStore.js')

    await chatStore.handleNewSession()

    expect(dialogStore.confirmDialog).toHaveBeenCalled()
    expect(api.postCreateSession).toHaveBeenCalled()
    expect(api.postCreateTestSession).not.toHaveBeenCalled()
  })

  it("the test store's handleNewSession calls postCreateTestSession, scoped to the project, skipping confirmation", async () => {
    api.postCreateTestSession.mockResolvedValue({ id: 4, active: true })
    api.getCurrentTestSession.mockResolvedValue({ id: 4, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })
    const dialogStore = await import('../src/dialogStore.js')

    await testChatStore.handleNewSession()

    expect(dialogStore.confirmDialog).not.toHaveBeenCalled()
    expect(api.postCreateTestSession).toHaveBeenCalledWith('my-project')
    expect(api.postCreateSession).not.toHaveBeenCalled()
  })

  it("the live store's handleNewSession never runs an on-enter off the response — init-action's on-enter arrives over the websocket", async () => {
    api.postCreateSession.mockResolvedValue({ id: 5, active: true })
    api.getCurrentSession.mockResolvedValue({ id: 5, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })

    const onEnterActions = await import('../src/onEnterActions.js')
    await chatStore.handleNewSession()

    expect(onEnterActions.runOnEnterScript).not.toHaveBeenCalled()
  })

  it("the test store's handleReset calls postResetTestSessions and applies the returned state as-is (its on-enter arrives over the websocket)", async () => {
    api.postResetTestSessions.mockResolvedValue({ key: 'a', ui_label: 'A', actions: [] })
    api.getCurrentTestSession.mockResolvedValue({ id: 6, active: true, state: { key: 'a', ui_label: 'A', actions: [] } })

    const onEnterActions = await import('../src/onEnterActions.js')
    await testChatStore.handleReset()

    expect(api.postResetTestSessions).toHaveBeenCalledWith('my-project')
    expect(onEnterActions.runOnEnterScript).not.toHaveBeenCalled()
    expect(testChatStore.state.value).toEqual({ key: 'a', ui_label: 'A', actions: [] })
  })

  it('the live store has no handleReset at all — nothing in the real app ever calls it', () => {
    expect(chatStore.liveStore.handleReset).toBeNull()
  })

  it("the live store's handleCloseSession closes the current session, no confirmation, and reflects the closed state back", async () => {
    api.getCurrentSession.mockResolvedValue({ id: 7, active: true, state: { key: 'x', ui_label: 'X', actions: [] } })
    await chatStore.loadMessages()
    const dialogStore = await import('../src/dialogStore.js')

    api.postCloseSession.mockResolvedValue({ id: 7, active: false })
    await chatStore.handleCloseSession()

    expect(dialogStore.confirmDialog).not.toHaveBeenCalled()
    expect(api.postCloseSession).toHaveBeenCalledWith(7)
    expect(chatStore.selectedSessionActive.value).toBe(false)
  })

  it('handleCloseSession is a no-op with no current session yet', async () => {
    await chatStore.handleCloseSession()

    expect(api.postCloseSession).not.toHaveBeenCalled()
  })
})
