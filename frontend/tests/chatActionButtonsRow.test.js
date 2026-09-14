import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getMessages: vi.fn(),
}))
vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))

describe('pressing a chat button', () => {
  let chatStore, bus

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    chatStore = await import('../src/chatStore.js')
    chatStore.currentSessionId.value = 1
    bus.deliver({ type: 'state.buttons', session_id: 1, actions: [{ name: 'yes', ui_button: 'Yes' }, { name: 'no', ui_button: 'No' }] })
  })

  it('keeps the whole row disabled until the server sends the next row', () => {
    chatStore.handleAction('yes')

    expect(chatStore.actionLoading.value).toBe(true)

    bus.deliver({ type: 'state.buttons', session_id: 1, actions: [{ name: 'more', ui_button: 'More' }] })

    expect(chatStore.actionLoading.value).toBe(false)
    expect(chatStore.liveStore.buttons.value.map((b) => b.name)).toEqual(['more'])
  })

  it('re-enables the row when the state changes and the row is cleared', () => {
    chatStore.handleAction('yes')
    bus.deliver({ type: 'state.changed', session_id: 1, state: { key: 's2' } })

    expect(chatStore.actionLoading.value).toBe(false)
    expect(chatStore.liveStore.buttons.value).toEqual([])
  })

  it('leaves the row enabled when nothing was sent', () => {
    bus.busChannel.send.mockReturnValueOnce(false)
    chatStore.handleAction('yes')

    expect(chatStore.actionLoading.value).toBe(false)
  })
})
