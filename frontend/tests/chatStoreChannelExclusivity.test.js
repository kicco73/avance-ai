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
vi.mock('../src/dialogStore.js', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))

const STATE = { key: 'x', ui_label: 'X', actions: [] }

describe('a conversation being had on another channel', () => {
  let chatStore
  let bus
  let api

  beforeEach(async () => {
    vi.resetModules()
    bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    chatStore = await import('../src/chatStore.js')
    const { installChatChannel } = await import('../src/liveChatChannel.js')
    installChatChannel('webchat')
    api = await import('../src/api.js')
    api.getHistory.mockResolvedValue([])
    api.getSessions.mockResolvedValue([])
    await chatStore.loadMessages('proj')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('is shown, and is not writable', async () => {
    bus.deliverEntered({ sessionId: 7, projectId: 'proj', state: STATE, channel: 'whatsapp', current: false })

    expect(chatStore.currentSessionId.value).toBe(7)
    expect(chatStore.selectedSessionActive.value).toBe(false)
    expect(chatStore.liveStore.conversationElsewhere.value).toBe(true)
  })

  it('is told apart from this chat’s own conversation, active or closed', async () => {
    bus.deliverEntered({ sessionId: 7, projectId: 'proj', state: STATE })

    expect(chatStore.selectedSessionActive.value).toBe(true)
    expect(chatStore.liveStore.conversationElsewhere.value).toBe(false)

    bus.deliver({ type: 'session.ended', session_id: 7, reason: 'user' })

    expect(chatStore.selectedSessionActive.value).toBe(false)
    expect(chatStore.liveStore.conversationElsewhere.value).toBe(false)
  })

  it('is handed over the moment the other channel takes it', async () => {
    bus.deliverEntered({ sessionId: 7, projectId: 'proj', state: STATE })
    expect(chatStore.selectedSessionActive.value).toBe(true)

    bus.deliver({ type: 'session.ended', session_id: 7, reason: 'channel-switch' })

    expect(chatStore.selectedSessionActive.value).toBe(false)
  })

  it('is taken back by asking for a conversation of this chat’s own', async () => {
    bus.deliverEntered({ sessionId: 7, projectId: 'proj', state: STATE, channel: 'whatsapp', current: false })

    await chatStore.handleNewSession()

    const sent = bus.busChannel.send.mock.calls.map(([frame]) => frame)
    expect(sent.filter((frame) => frame.type === 'session.create')).toEqual([
      { type: 'session.create', project_id: 'proj', session_type: 'live' },
    ])

    bus.deliverEntered({ sessionId: 8, projectId: 'proj', state: STATE })

    expect(chatStore.currentSessionId.value).toBe(8)
    expect(chatStore.selectedSessionActive.value).toBe(true)
    expect(chatStore.liveStore.conversationElsewhere.value).toBe(false)
  })

  it('is never what a conversation with no channel at all looks like', async () => {
    bus.deliverEntered({ sessionId: 7, projectId: 'proj', state: STATE, channel: null })

    expect(chatStore.liveStore.conversationElsewhere.value).toBe(false)
    expect(chatStore.selectedSessionActive.value).toBe(true)
  })
})
