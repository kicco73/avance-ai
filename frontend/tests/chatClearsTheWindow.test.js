import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
}))
vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))

describe("an on-exit's chat.clear()", () => {
  let chatStore, bus

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    chatStore = await import('../src/chatStore.js')
    chatStore.currentSessionId.value = 1
    bus.deliver({
      type: 'session.messages',
      session_id: 1,
      messages: [
        { id: 1, role: 'user', content: 'hello' },
        { id: 2, role: 'assistant', content: 'hi there' },
      ],
    })
  })

  const shown = () => chatStore.liveStore.messages.value.map((m) => m.content)

  it('leaves the window blank, so the next reply is the first message in it', () => {
    expect(shown()).toEqual(['hello', 'hi there'])

    bus.deliver({ type: 'ui.notification', session_id: 1, task: 'clear()' })

    expect(shown()).toEqual([])

    bus.deliver({ type: 'output.text', session_id: 1, text: 'a fresh start', assistant_message_id: 3 })

    expect(shown()).toEqual(['a fresh start'])
  })

  it('leaves the window of another conversation alone', () => {
    bus.deliver({ type: 'ui.notification', session_id: 2, task: 'clear()' })

    expect(shown()).toEqual(['hello', 'hi there'])
  })
})
