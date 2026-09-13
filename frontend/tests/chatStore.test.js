// An action's own "task" script never rides in anything a chat asked
// for: the backend runs it as a task and publishes its output as a
// `ui.notification`, which notificationBus.js runs exactly once,
// globally, whichever chat stores happen to exist. taskActions.js itself
// (script → taskLocals binding) has its own dedicated tests — see
// taskActions.test.js.
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
    await import('../src/notificationBus.js')
  })

  afterEach(() => {
    vi.doUnmock('../src/busChannel.js')
    vi.clearAllMocks()
  })

  // What the channel handed the bus: it subscribes the first time
  // anyone subscribes to it.
  function pushedFrame() {
    const call = busChannel.subscribe.mock.calls.find(([type]) => type === 'ui.notification')
    expect(call).toBeTruthy()
    return call[1]
  }

  it('runs the script of a frame carrying only "task" (an ActionTask that ran server-side)', async () => {
    const bus = await import('../src/notificationBus.js')
    const seen = []
    bus.subscribeToStateNotifications((frame) => seen.push(frame))

    pushedFrame()({ project_name: undefined, state: undefined, 'task': "notify('Nice!', 'You reached **state B**.')" })

    expect(taskActions.runTaskScript).toHaveBeenCalledTimes(1)
    expect(taskActions.runTaskScript).toHaveBeenCalledWith("notify('Nice!', 'You reached **state B**.')")
    expect(seen).toEqual([])  // no state: nothing for the stores
  })

  it('runs the script once however many stores subscribed, and hands the state to each', async () => {
    const bus = await import('../src/notificationBus.js')
    const a = []
    const b = []
    bus.subscribeToStateNotifications((frame) => a.push(frame))
    bus.subscribeToStateNotifications((frame) => b.push(frame))

    pushedFrame()({ project_name: 'proj', state: { key: 'x' }, 'task': 'celebrate()' })

    expect(taskActions.runTaskScript).toHaveBeenCalledTimes(1)
    expect(a).toEqual([{ project_name: 'proj', state: { key: 'x' } }])
    expect(b).toEqual([{ project_name: 'proj', state: { key: 'x' } }])
  })

  it('a live chat store applies a pushed state only when it is about its own project', async () => {
    const chatStore = await import('../src/chatStore.js')
    chatStore.currentProjectId.value = 'proj'
    const push = pushedFrame()

    push({ project_name: 'other', state: { key: 'x', actions: [] } })
    expect(chatStore.state.value?.key).not.toBe('x')

    push({ project_name: 'proj', state: { key: 'x', actions: [] } })
    expect(chatStore.state.value.key).toBe('x')
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
    // Regression: an earlier version mutated the raw `message` object the
    // store holds directly (message.reaction = ...) instead of replacing
    // its slot in messages.value — that bypasses Vue's reactive proxy
    // entirely, so the bubble never re-rendered until something else (e.g.
    // a full reload) rebuilt messages.value from scratch.
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
    // Regression test: the streaming assistant bubble's own local
    // `timestamp` used to be stamped at the same instant as the user
    // message that triggered it. A second message sent while that stale
    // timestamp was still fresh could then collide with (or trail only
    // slightly behind) the next user message's own timestamp — and
    // buildTimeline's own tie-break (a message always sorts before a
    // same-effective-moment transition) then pushed the transition past
    // bubbles it should have preceded. Reproduced directly against a live
    // "before" mode session (autotracking_on_ai_message=False): the first
    // transition rendered fine, the second landed after the assistant's
    // reply instead of right after the user's own message. The bubble is
    // now opened — and timestamped — when the system says it has started
    // writing, which is genuinely later.
    await chatStore.handleSend('turn 1')
    deliver({ type: 'output.reaction', session_id: 1, user_message_id: 3, reaction: null })
    vi.advanceTimersByTime(2000) // the AI reply genuinely takes real time
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 4, text: 'Reply one.' })

    vi.advanceTimersByTime(20000) // the user takes real time to type turn 2

    await chatStore.handleSend('turn 2')
    deliver({ type: 'output.reaction', session_id: 1, user_message_id: 5, reaction: null })
    vi.advanceTimersByTime(2000)
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 6, text: 'Reply two.' })

    // EditProjectView.vue's own rawLiveMessages mapping, reproduced here
    // so this exercises the real chatStore state against the real
    // buildTimeline, the same combination the live bug surfaced through.
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
