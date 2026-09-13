// Regression coverage for chatStoreFactory.js's createChatStore: the live
// chat (chatStore.js) and EditProjectView.vue's own embedded "Run" test
// chat (testChatStore.js) are two independent instances, each always
// asking for its own kind of conversation, for its own project, and
// listing its own pool of sessions — never a shared flag deciding which
// one a single store instance happens to be routed to right now.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../busChannel.js', () => import('../../../../tests/fakeBus.js'))
vi.mock('../../../taskActions.js', () => ({ runTaskScript: vi.fn() }))
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
  projectFileContentUrl: vi.fn(() => '/skin.css')
}))
vi.mock('../../../dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

const STATE = { key: 'x', ui_label: 'X', actions: [] }

describe('the live store and the test store each ask for their own conversation', () => {
  let chatStore
  let testChatStore
  let api
  let bus
  let dialogStore
  let taskActions

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('../../../../tests/fakeBus.js')
    bus.resetFakeBus()
    chatStore = await import('../../../chatStore.js')
    testChatStore = await import('../testChatStore.js')
    testChatStore.setTestProject('my-project')
    api = await import('../../../api.js')
    dialogStore = await import('../../../dialogStore.js')
    taskActions = await import('../../../taskActions.js')
    api.getHistory.mockResolvedValue([])
    api.getSessions.mockResolvedValue([])
    api.getTestSessions.mockResolvedValue([])
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  function sentOfType(type) {
    return bus.busChannel.send.mock.calls.map(([frame]) => frame).filter((frame) => frame.type === type)
  }

  it('loadMessages asks for its own kind of conversation, and loadSessions lists its own pool', async () => {
    await chatStore.loadMessages('live-project')
    await chatStore.loadSessions()

    expect(sentOfType('session.enter')).toEqual([
      { type: 'session.enter', project_id: 'live-project', session_type: 'live' },
    ])
    expect(api.getSessions).toHaveBeenCalled()
    expect(api.getTestSessions).not.toHaveBeenCalled()

    await testChatStore.loadMessages()
    await testChatStore.loadSessions()

    expect(sentOfType('session.enter')[1]).toEqual({
      type: 'session.enter', project_id: 'my-project', session_type: 'test',
    })
    expect(api.getTestSessions).toHaveBeenCalledWith('my-project')
  })

  it('has no conversation at all until the one it asked for answers', async () => {
    await chatStore.loadMessages('live-project')
    expect(chatStore.currentSessionId.value).toBeNull()

    bus.deliverEntered({ sessionId: 1, projectId: 'live-project', state: STATE })

    expect(chatStore.currentSessionId.value).toBe(1)
    // The test store was never asked about, and took nothing.
    expect(testChatStore.currentSessionId.value).toBeNull()
  })

  it('handleNewSession asks for a new one of its own kind — the live one confirming first, the test one scoped to its project and unconfirmed', async () => {
    await chatStore.loadMessages('live-project')
    bus.deliverEntered({ sessionId: 1, projectId: 'live-project', state: STATE })

    await chatStore.handleNewSession()

    expect(dialogStore.confirmDialog).toHaveBeenCalled()
    expect(sentOfType('session.create')).toEqual([
      { type: 'session.create', project_id: 'live-project', session_type: 'live' },
    ])
    // init-action's own task arrives as a notification, never off anything
    // the chat asked for.
    expect(taskActions.runTaskScript).not.toHaveBeenCalled()

    dialogStore.confirmDialog.mockClear()

    await testChatStore.handleNewSession()

    expect(dialogStore.confirmDialog).not.toHaveBeenCalled()
    expect(sentOfType('session.create')[1]).toEqual({
      type: 'session.create', project_id: 'my-project', session_type: 'test',
    })
  })

  it('only the test store can reset, applying the returned state as-is, and the live store has no handleReset at all', async () => {
    expect(chatStore.liveStore.handleReset).toBeNull()

    api.postResetTestSessions.mockResolvedValue({ key: 'a', ui_label: 'A', actions: [] })

    await testChatStore.handleReset()

    expect(api.postResetTestSessions).toHaveBeenCalledWith('my-project')
    // Its task arrives as a notification too.
    expect(taskActions.runTaskScript).not.toHaveBeenCalled()
    expect(testChatStore.state.value).toEqual({ key: 'a', ui_label: 'A', actions: [] })
  })

  it('handleCloseSession terminates the current session unconfirmed, and is a no-op with no session yet', async () => {
    chatStore.handleCloseSession()
    expect(sentOfType('session.terminate')).toEqual([])

    await chatStore.loadMessages('live-project')
    bus.deliverEntered({ sessionId: 7, projectId: 'live-project', state: STATE })

    chatStore.handleCloseSession()

    expect(dialogStore.confirmDialog).not.toHaveBeenCalled()
    expect(sentOfType('session.terminate')).toEqual([{ type: 'session.terminate', session_id: 7 }])

    // Closed — said by the one place every closure passes through.
    bus.deliver({ type: 'session.ended', session_id: 7, reason: 'user' })
    expect(chatStore.selectedSessionActive.value).toBe(false)
    // And the test chat, which was never in that conversation, is untouched.
    expect(testChatStore.selectedSessionActive.value).toBe(true)
  })
})
