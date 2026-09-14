import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../api.js', () => ({
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getHistory: vi.fn(),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  postTruncateSession: vi.fn(),
  deleteSession: vi.fn(),
}))

describe('the event skill, as the registry sees it', () => {
  it('names itself after its own directory and contributes one live-chat observer', async () => {
    const manifest = await import('../index.js')
    const { liveChatWakeup } = await import('../liveChatWakeup.js')

    expect(manifest.key).toBe('event')
    expect(manifest.liveChatObservers).toEqual([liveChatWakeup])
  })
})

describe('a wake-up pushed for another project reaches the live conversation', () => {
  let busChannel
  let chatStore
  let subscribedBefore

  beforeEach(async () => {
    vi.resetModules()
    vi.doMock('../../../busChannel.js', () => ({ busChannel: { subscribe: vi.fn(() => () => {}), onConnectionState: vi.fn(() => () => {}), send: vi.fn(() => true), connectionState: 'open' } }))
    ;({ busChannel } = await import('../../../busChannel.js'))
    chatStore = await import('../../../chatStore.js')
    const { liveChatWakeup } = await import('../liveChatWakeup.js')
    subscribedBefore = notificationSubscriptions().length
    chatStore.observeLiveChat([liveChatWakeup])
  })

  afterEach(() => {
    vi.doUnmock('../../../busChannel.js')
    vi.clearAllMocks()
  })

  function notificationSubscriptions() {
    return busChannel.subscribe.mock.calls.filter(([type]) => type === 'ui.notification')
  }

  function pushedFrame() {
    const call = notificationSubscriptions()[subscribedBefore]
    expect(call).toBeTruthy()
    return call[1]
  }

  it('applies a pushed state only when it is about the project on screen', () => {
    chatStore.currentProjectId.value = 'proj'
    const push = pushedFrame()

    push({ project_name: 'other', state: { key: 'x', actions: [] } })
    expect(chatStore.state.value?.key).not.toBe('x')

    push({ project_name: 'proj', state: { key: 'x', actions: [] } })
    expect(chatStore.state.value.key).toBe('x')
  })

  it('leaves a frame carrying only a task to whoever runs those', () => {
    chatStore.currentProjectId.value = 'proj'
    const before = chatStore.state.value

    pushedFrame()({ project_name: 'proj', task: 'celebrate()' })

    expect(chatStore.state.value).toBe(before)
  })

  it('watches one conversation once, however many times the boot installs it', async () => {
    const { liveChatWakeup } = await import('../liveChatWakeup.js')

    chatStore.observeLiveChat([liveChatWakeup])
    chatStore.observeLiveChat([liveChatWakeup])

    expect(notificationSubscriptions()).toHaveLength(subscribedBefore + 1)
  })
})
