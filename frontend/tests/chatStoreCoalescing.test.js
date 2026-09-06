// The input stays open while the model answers, so a user can send again
// before the previous reply lands. Every send gets its own bubble and its
// own placeholder; the turn that answers several messages at once
// reconciles one of them, and the requests it answered for report no
// reply of their own — which drops their placeholder (see the backend's
// own coalescing, and submitMessage's assistant_message_id branch).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
  getSessionState: vi.fn(),
}))
vi.mock('../src/chatClient.js', () => ({
  sendMessage: vi.fn(),
  getConnectionState: vi.fn(() => 'open'),
  onConnectionState: vi.fn(() => () => {}),
  resolvePendingTurnsAfterReload: vi.fn(),
}))

const STATE = { key: 'a', ui_label: 'A', actions: [] }

function answered(id, content) {
  return {
    reply: [{ id, content, timestamp: 't' }],
    user_message_id: id - 1,
    assistant_message_id: id,
    state: STATE,
    'on-enter': null,
    session_id: 1,
  }
}

function alreadyAnswered(userMessageId) {
  return {
    reply: [],
    user_message_id: userMessageId,
    assistant_message_id: null,
    state: STATE,
    'on-enter': null,
    session_id: 1,
  }
}

describe('several messages sent while a turn is still running', () => {
  let chatStore
  let chatClient

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    chatClient = await import('../src/chatClient.js')
    chatStore.currentSessionId.value = 1
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('keeps a bubble per send, reconciles the replies, and drops the placeholder of a turn answered by another', async () => {
    const pending = []
    chatClient.sendMessage.mockImplementation(() => new Promise((resolve) => pending.push(resolve)))

    const first = chatStore.handleSend('I have a problem')
    const second = chatStore.handleSend('with flight VY3003')
    const third = chatStore.handleSend('leaving tomorrow')

    // All three are on screen at once, in send order, while nothing has
    // been answered yet — the input never closed.
    expect(chatStore.messages.value.filter((m) => m.role === 'user').map((m) => m.content)).toEqual([
      'I have a problem', 'with flight VY3003', 'leaving tomorrow',
    ])
    expect(chatStore.chatLoading.value).toBe(true)

    pending[0](answered(11, 'Let me look.'))
    pending[1](answered(21, 'Found it: on time.'))
    pending[2](alreadyAnswered(20))
    await Promise.all([first, second, third])

    // Each send keeps its own bubble and its own placeholder, so a reply
    // lands where its send was — the user bubbles stay in send order, and
    // the third placeholder, whose turn was answered by the second, is
    // gone rather than left empty.
    const rendered = chatStore.messages.value.map((m) => [m.role, m.content])
    expect(rendered).toEqual([
      ['user', 'I have a problem'],
      ['assistant', 'Let me look.'],
      ['user', 'with flight VY3003'],
      ['assistant', 'Found it: on time.'],
      ['user', 'leaving tomorrow'],
    ])
    expect(chatStore.chatLoading.value).toBe(false)
  })

  it('counts turns rather than latching, so chatLoading only clears once the last one lands', async () => {
    const pending = []
    chatClient.sendMessage.mockImplementation(() => new Promise((resolve) => pending.push(resolve)))

    const first = chatStore.handleSend('one')
    const second = chatStore.handleSend('two')

    pending[0](answered(11, 'first reply'))
    await first
    expect(chatStore.chatLoading.value).toBe(true)

    pending[1](answered(21, 'second reply'))
    await second
    expect(chatStore.chatLoading.value).toBe(false)
  })
})
