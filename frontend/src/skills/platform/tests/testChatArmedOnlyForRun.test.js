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
  getAiModels: vi.fn(),
  postAiModelSelection: vi.fn(),
  postResetTestSessions: vi.fn(),
  postTruncateSession: vi.fn(),
  getTestChatModels: vi.fn(),
  postTestChatModelSelection: vi.fn(),
  projectFileContentUrl: vi.fn(() => '/skin.css')
}))
vi.mock('../../../dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

describe('the test conversation is only asked for once Run is open', () => {
  let testChatStore
  let bus

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('../../../../tests/fakeBus.js')
    bus.resetFakeBus()
    testChatStore = await import('../testChatStore.js')
    const api = await import('../../../api.js')
    api.getHistory.mockResolvedValue([])
    api.getTestSessions.mockResolvedValue([])
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  function entered() {
    return bus.busChannel.send.mock.calls
      .map(([frame]) => frame)
      .filter((frame) => frame.type === 'session.enter' || frame.type === 'session.create')
  }

  it('asks for nothing while the editor is in Design', () => {
    bus.deliverConnected()

    expect(entered()).toEqual([])
  })

  it('asks for its conversation once Run arms it, on entering and on every socket that follows', async () => {
    testChatStore.setTestProject('my-project')
    await testChatStore.loadMessages()

    expect(entered()).toEqual([
      { type: 'session.enter', project_id: 'my-project', session_type: 'test' },
    ])

    bus.deliverConnected()

    expect(entered()).toHaveLength(2)
  })

  it('goes quiet again when the editor is left', async () => {
    testChatStore.setTestProject('my-project')
    await testChatStore.loadMessages()
    bus.deliverEntered({ sessionId: 7, projectId: 'my-project', sessionType: 'test' })

    testChatStore.releaseTestProject()
    bus.busChannel.send.mockClear()
    bus.deliverConnected()

    expect(entered()).toEqual([])
  })
})
