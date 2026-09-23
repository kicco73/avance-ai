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

  it('puts env-<key>-<value> on the window and follows every later change of that key', () => {
    bus.deliver({ type: 'ui.notification', session_id: 1, task: 'bind_env("mood", "happy")\nbind_env("level", 2)' })

    expect(classes()).toEqual(['env-mood-happy', 'env-level-2'])

    bus.deliver({ type: 'env.changed', session_id: 1, key: 'mood', value: 'very sad' })
    bus.deliver({ type: 'env.changed', session_id: 1, key: 'other', value: 'x' })
    bus.deliver({ type: 'env.changed', session_id: 2, key: 'level', value: 9 })

    expect(classes()).toEqual(['env-mood-very_sad', 'env-level-2'])
  })

  it('drops every binding on unbind_env_all, and stops following', () => {
    bus.deliver({ type: 'ui.notification', session_id: 1, task: 'bind_env("mood", "happy")' })
    bus.deliver({ type: 'ui.notification', session_id: 1, task: 'unbind_env_all()' })
    bus.deliver({ type: 'env.changed', session_id: 1, key: 'mood', value: 'sad' })

    expect(classes()).toEqual([])
  })

  it('drops every binding when the window moves to another conversation', async () => {
    bus.deliver({ type: 'ui.notification', session_id: 1, task: 'bind_env("mood", "happy")' })

    chatStore.currentSessionId.value = 2
    await Promise.resolve()

    expect(classes()).toEqual([])
  })
})
