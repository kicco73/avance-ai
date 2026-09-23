import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
}))
vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))

describe("an on-exit's chat.bind_env()", () => {
  let chatStore, bus

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    chatStore = await import('../src/chatStore.js')
    chatStore.currentSessionId.value = 1
  })

  const classes = () => chatStore.liveStore.envClasses.value

  it('puts env-<key>-<value> on the window for every value the server says is bound', () => {
    bus.deliver({ type: 'env.bindings', session_id: 1, values: { mood: 'happy', level: 2 } })

    expect(classes()).toEqual(['env-mood-happy', 'env-level-2'])

    bus.deliver({ type: 'env.bindings', session_id: 1, values: { mood: 'very sad', level: 2 } })

    expect(classes()).toEqual(['env-mood-very_sad', 'env-level-2'])
  })

  it('shows no class for a key bound while still empty', () => {
    bus.deliver({ type: 'env.bindings', session_id: 1, values: { mood: '' } })

    expect(classes()).toEqual([])
  })

  it('drops every class when the server says nothing is bound any more', () => {
    bus.deliver({ type: 'env.bindings', session_id: 1, values: { mood: 'happy' } })
    bus.deliver({ type: 'env.bindings', session_id: 1, values: {} })

    expect(classes()).toEqual([])
  })

  it("never shows another conversation's bindings", async () => {
    bus.deliver({ type: 'env.bindings', session_id: 1, values: { mood: 'happy' } })

    chatStore.currentSessionId.value = 2
    await Promise.resolve()

    expect(classes()).toEqual([])
  })
})
