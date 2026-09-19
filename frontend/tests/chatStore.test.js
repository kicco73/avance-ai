import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
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
  projectFileContentUrl: vi.fn(() => '/skin.css')
}))
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

const STATE = { key: 'x', ui_label: 'X', actions: [] }

describe('a pushed task script runs once, in the chat whose conversation it is about', () => {
  let bus
  let taskActions
  let chatStore
  let testChatStore

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    chatStore = await import('../src/chatStore.js')
    const { createChatStore } = await import('../src/chatStoreFactory.js')
    testChatStore = createChatStore({ kind: 'test', getSessionsList: vi.fn().mockResolvedValue([]) })
    testChatStore.setProject('proj')
    taskActions = await import('../src/taskActions.js')
    const api = await import('../src/api.js')
    api.getHistory.mockResolvedValue([])
    api.getSessions.mockResolvedValue([])
    api.getTestSessions.mockResolvedValue([])
    api.getActuators.mockResolvedValue({ enabled: true })
    await chatStore.loadMessages('proj')
    bus.deliverEntered({ sessionId: 7, projectId: 'proj', state: STATE })
    await testChatStore.loadMessages()
    bus.deliverEntered({ sessionId: 8, projectId: 'proj', sessionType: 'test', state: STATE })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('runs the script of a frame about the live conversation, once', () => {
    bus.deliver({ type: 'ui.notification', session_id: 7, project_id: 'proj', task: "notify('Nice!', 'Done.')" })

    expect(taskActions.runTaskScript).toHaveBeenCalledTimes(1)
    expect(taskActions.runTaskScript).toHaveBeenCalledWith("notify('Nice!', 'Done.')", { playBackgroundAudio: expect.any(Function) })
  })

  it('runs the script of a frame about the test conversation too — the "Run" chat is a chat like any other', () => {
    bus.deliver({ type: 'ui.notification', session_id: 8, project_id: 'proj', task: 'celebrate()' })

    expect(taskActions.runTaskScript).toHaveBeenCalledTimes(1)
    expect(taskActions.runTaskScript).toHaveBeenCalledWith('celebrate()', { playBackgroundAudio: expect.any(Function) })
  })

  it('says nothing for a script about a conversation nobody has open', () => {
    bus.deliver({ type: 'ui.notification', session_id: 9, project_id: 'proj', task: 'celebrate()' })

    expect(taskActions.runTaskScript).not.toHaveBeenCalled()
  })

  it('falls back to the project when the script belongs to no conversation (a deferred call)', () => {
    bus.deliver({ type: 'ui.notification', project_id: 'other', task: 'celebrate()' })
    expect(taskActions.runTaskScript).not.toHaveBeenCalled()

    bus.deliver({ type: 'ui.notification', project_id: 'proj', task: 'show("hi")' })
    expect(taskActions.runTaskScript).toHaveBeenCalledWith('show("hi")', { playBackgroundAudio: expect.any(Function) })
  })

  it('has nothing to say about a frame carrying no task', () => {
    bus.deliver({ type: 'ui.notification', session_id: 7, project_id: 'proj', state: { key: 'x' } })

    expect(taskActions.runTaskScript).not.toHaveBeenCalled()
  })
})

describe('a store waiting for its session only takes a session.info of its own kind', () => {
  let bus
  let liveStore
  let testStore

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    const { createChatStore } = await import('../src/chatStoreFactory.js')
    const api = await import('../src/api.js')
    api.getHistory.mockResolvedValue([])
    api.getSessions.mockResolvedValue([])
    api.getTestSessions.mockResolvedValue([])
    liveStore = createChatStore({ kind: 'live', getSessionsList: vi.fn().mockResolvedValue([]) })
    testStore = createChatStore({ kind: 'test', getSessionsList: vi.fn().mockResolvedValue([]) })
    liveStore.setProject('proj')
    testStore.setProject('proj')
    await liveStore.loadMessages()
    await testStore.loadMessages()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('leaves the test conversation to the test store, buttons and all', () => {
    bus.deliverEntered({ sessionId: 8, projectId: 'proj', sessionType: 'test', state: STATE, actions: [{ name: 'go' }] })

    expect(testStore.currentSessionId.value).toBe(8)
    expect(testStore.buttons.value).toEqual([{ name: 'go' }])
    expect(liveStore.currentSessionId.value).toBeNull()

    bus.deliverEntered({ sessionId: 7, projectId: 'proj', state: STATE, actions: [{ name: 'start' }] })

    expect(liveStore.currentSessionId.value).toBe(7)
    expect(liveStore.buttons.value).toEqual([{ name: 'start' }])
    expect(testStore.currentSessionId.value).toBe(8)
    expect(testStore.buttons.value).toEqual([{ name: 'go' }])
  })
})
