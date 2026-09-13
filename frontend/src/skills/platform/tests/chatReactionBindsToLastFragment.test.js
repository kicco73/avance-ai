// A reply can cover several messages sent in a breath, and everything
// that binds to "the user's message" binds to the LAST of them — the
// reaction included (see backend/src/docs/PROJECT_SPECS.md §0.1, and
// turn/outbound.py's own reacted(), which puts that last id on the frame).
// The store used to stamp it on the first bubble still waiting for an id,
// so the badge landed on the wrong message and the real last one never
// learned its own id.
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../../../api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
}))
vi.mock('../api.js', () => ({
  getCurrentTestSession: vi.fn(),
  postCreateTestSession: vi.fn(),
  getTestSessions: vi.fn(),
  postResetTestSessions: vi.fn(),
  getTestChatModels: vi.fn(),
  postTestChatModelSelection: vi.fn(),
  postSessionAction: vi.fn(),
  getHistory: vi.fn(async () => []),
  getSessions: vi.fn(async () => []),
  getSessionSignals: vi.fn(async () => []),
}))
vi.mock('../../../busChannel.js', () => import('../../../../tests/fakeBus.js'))

describe('the reaction to a coalesced message', () => {
  let testStore
  let deliver

  beforeEach(async () => {
    vi.resetModules()
    const bus = await import('../../../../tests/fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    testStore = (await import('../testChatStore.js')).testStore
    testStore.currentSessionId.value = 5
  })

  function userBubbles() {
    return testStore.messages.value.filter((m) => m.role === 'user')
  }

  it('lands on the last message the one reply covered, not the first', async () => {
    await testStore.handleSend('ciao')
    await testStore.handleSend('anzi aspetta')

    deliver({ type: 'output.reaction', session_id: 5, user_message_id: 77, reaction: 'supportive' })

    expect(userBubbles()).toHaveLength(2)
    expect(userBubbles()[0].content).toBe('ciao')
    expect(userBubbles()[0].messageId ?? null).toBeNull()
    expect(userBubbles()[0].reaction ?? null).toBeNull()
    expect(userBubbles()[1]).toMatchObject({
      content: 'anzi aspetta', messageId: 77, reaction: 'supportive',
    })
  })

  it('still binds a lone message to itself', async () => {
    await testStore.handleSend('ciao')

    deliver({ type: 'output.reaction', session_id: 5, user_message_id: 42, reaction: 'supportive' })

    expect(userBubbles()).toHaveLength(1)
    expect(userBubbles()[0]).toMatchObject({ content: 'ciao', messageId: 42, reaction: 'supportive' })
  })
})
