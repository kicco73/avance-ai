import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { buildTimeline } from '../src/testTimeline.js'

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

describe('the notification bus runs a pushed task script once, globally', () => {
  let taskActions
  let busChannel

  beforeEach(async () => {
    vi.resetModules()
    vi.doMock('../src/busChannel.js', () => ({ busChannel: { subscribe: vi.fn(() => () => {}), onConnectionState: vi.fn(() => () => {}), send: vi.fn(() => true), connectionState: 'open' } }))
    taskActions = await import('../src/taskActions.js')
    ;({ busChannel } = await import('../src/busChannel.js'))
    const bus = await import('../src/notificationBus.js')
    bus.watchPushedTasks()
  })

  afterEach(() => {
    vi.doUnmock('../src/busChannel.js')
    vi.clearAllMocks()
  })

  function pushedFrame() {
    const call = busChannel.subscribe.mock.calls.find(([type]) => type === 'ui.notification')
    expect(call).toBeTruthy()
    return call[1]
  }

  it('runs the script of a frame carrying only "task" (an ActionTask that ran server-side)', async () => {
    pushedFrame()({ project_name: undefined, state: undefined, 'task': "notify('Nice!', 'You reached **state B**.')" })

    expect(taskActions.runTaskScript).toHaveBeenCalledTimes(1)
    expect(taskActions.runTaskScript).toHaveBeenCalledWith("notify('Nice!', 'You reached **state B**.')")
  })

  it('subscribes once however many times the boot asks for it', async () => {
    const bus = await import('../src/notificationBus.js')
    bus.watchPushedTasks()
    bus.watchPushedTasks()

    pushedFrame()({ 'task': 'celebrate()' })

    expect(busChannel.subscribe.mock.calls.filter(([type]) => type === 'ui.notification')).toHaveLength(1)
    expect(taskActions.runTaskScript).toHaveBeenCalledTimes(1)
  })

  it('has nothing to say about a frame carrying no task', async () => {
    pushedFrame()({ project_name: 'proj', state: { key: 'x' } })

    expect(taskActions.runTaskScript).not.toHaveBeenCalled()
  })
})

describe('the person\'s own message is stamped by what the system says about it', () => {
  let chatStore
  let deliver

  beforeEach(async () => {
    vi.resetModules()
    vi.doMock('../src/busChannel.js', () => import('./fakeBus.js'))
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    chatStore = await import('../src/chatStore.js')
    chatStore.currentSessionId.value = 1
  })

  afterEach(() => {
    vi.doUnmock('../src/busChannel.js')
    vi.clearAllMocks()
  })

  it('stamps the local user bubble with the backend-assigned user_message_id', async () => {
    await chatStore.handleSend('hello')

    deliver({ type: 'output.reaction', session_id: 1, user_message_id: 42, reaction: null })

    const userMessage = chatStore.messages.value.find((m) => m.role === 'user')
    expect(userMessage.messageId).toBe(42)
  })

  it('applies the reaction live, via a replaced object (not a direct mutation)', async () => {
    await chatStore.handleSend('hello')
    const before = chatStore.messages.value.find((m) => m.role === 'user')

    deliver({ type: 'output.reaction', session_id: 1, user_message_id: 42, reaction: 'listening' })

    const userMessage = chatStore.messages.value.find((m) => m.role === 'user')
    expect(userMessage.reaction).toBe('listening')
    expect(userMessage).not.toBe(before)
  })

  it('says nothing about a message in a conversation that is not on screen', async () => {
    await chatStore.handleSend('hello')

    deliver({ type: 'output.reaction', session_id: 2, user_message_id: 42, reaction: 'listening' })

    const userMessage = chatStore.messages.value.find((m) => m.role === 'user')
    expect(userMessage.messageId).toBeUndefined()
    expect(userMessage.reaction).toBeUndefined()
  })
})

describe('every exchange stays correctly ordered against real buildTimeline', () => {
  let chatStore
  let deliver

  beforeEach(async () => {
    vi.resetModules()
    vi.useFakeTimers()
    vi.doMock('../src/busChannel.js', () => import('./fakeBus.js'))
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    chatStore = await import('../src/chatStore.js')
    chatStore.currentSessionId.value = 1
  })

  afterEach(() => {
    vi.doUnmock('../src/busChannel.js')
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('positions a second exchange\'s own transition after its own user message, not after the assistant reply', async () => {
    await chatStore.handleSend('turn 1')
    deliver({ type: 'output.reaction', session_id: 1, user_message_id: 3, reaction: null })
    vi.advanceTimersByTime(2000)
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 4, text: 'Reply one.' })

    vi.advanceTimersByTime(20000)

    await chatStore.handleSend('turn 2')
    deliver({ type: 'output.reaction', session_id: 1, user_message_id: 5, reaction: null })
    vi.advanceTimersByTime(2000)
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 6, text: 'Reply two.' })

    const rawLiveMessages = chatStore.messages.value.map((m) => ({
      id: m.messageId ?? null,
      timestamp: m.timestamp,
      role: m.role,
      content: m.content,
      audio_text: m.audioText
    }))
    const signalsLog = [
      { id: 1, timestamp: '2026-08-17 15:29:02.840779', old_state: '', new_state: 'Precontemplation', message_id: null, values: null, expected_state: null, expected_values: null, action: 'init-action' },
      { id: 4, timestamp: '2026-08-17 15:29:16.352393', old_state: 'Precontemplation', new_state: 'Contemplation', message_id: 3, values: '{"problemRecognition": 100}', expected_state: null, expected_values: null, action: 'raise awareness' },
      { id: 6, timestamp: '2026-08-17 15:29:36.455887', old_state: 'Contemplation', new_state: 'Preparation', message_id: 5, values: '{"decisionalBalanceShift": 90}', expected_state: null, expected_values: null, action: 'resolve ambivalence' }
    ]

    const timeline = buildTimeline(rawLiveMessages, signalsLog, 'Precontemplation', { includeSelfLoops: true })
    const order = timeline.map((e) => (e.kind === 'message' ? `m${e.message.id}` : `t->${e.transition.new_state}`))

    expect(order).toEqual(['t->Precontemplation', 'm3', 't->Contemplation', 'm4', 'm5', 't->Preparation', 'm6'])
  })
})
