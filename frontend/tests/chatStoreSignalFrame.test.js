import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getHistory: vi.fn(),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  postTruncateSession: vi.fn(),
  deleteSession: vi.fn(),
}))

const STATE_A = { key: 'a', ui_label: 'A', actions: [] }

describe('the signal values a turn ran on, pushed to the open conversation', () => {
  let chatStore
  let deliver
  let deliverEntered

  beforeEach(async () => {
    vi.resetModules()
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    deliverEntered = bus.deliverEntered
    chatStore = await import('../src/chatStore.js')
    await chatStore.selectSession({ id: 1, current: true })
    deliverEntered({ sessionId: 1, projectId: 'proj', state: STATE_A, messages: [] })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('reports what the turn measured, without waiting for a reply to arrive', () => {
    deliver({ type: 'state.signals', session_id: 1, values: { mood: 0.5, focus: 1 } })

    expect(chatStore.signalValues.value).toEqual({ mood: 0.5, focus: 1 })
  })

  it('ignores the values of a conversation other than the one open', () => {
    deliver({ type: 'state.signals', session_id: 1, values: { mood: 0.5 } })
    deliver({ type: 'state.signals', session_id: 2, values: { mood: 0.9 } })

    expect(chatStore.signalValues.value).toEqual({ mood: 0.5 })
  })
})
