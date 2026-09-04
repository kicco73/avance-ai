// An action's own "on-enter" script never rides in a turn's or a manual
// action's response any more: the backend runs it as a task and pushes
// its output over the websocket as a "notification" frame, which
// notificationBus.js runs exactly once, globally. These tests pin both
// halves: a store never runs a script off a response (even a stale one
// that still carries the old key), and the bus runs whatever frame
// arrives, whether or not it also carries a state for some project.
// onEnterActions.js itself (script → onEnterLocals binding) has its own
// dedicated tests — see onEnterActions.test.js.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { buildTimeline } from '../src/testTimeline.js'

vi.mock('../src/onEnterActions.js', () => ({ runOnEnterScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  postAction: vi.fn(),
  getSessions: vi.fn(),
  getAiModels: vi.fn()
}))
vi.mock('../src/chatClient.js', () => ({ sendMessage: vi.fn(), onNotification: vi.fn() }))

describe('handleAction (manual test action) never runs an on-enter script off the response', () => {
  let chatStore
  let onEnterActions
  let api

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    onEnterActions = await import('../src/onEnterActions.js')
    api = await import('../src/api.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('applies the state and runs nothing, even off a stale response still carrying "on-enter"', async () => {
    api.postAction.mockResolvedValue({
      reply: [],
      state: { key: 'b', ui_label: 'B', actions: [] },
      'on-enter': 'celebrate()',
      session_id: 1
    })

    await chatStore.handleAction('go-loud')

    expect(onEnterActions.runOnEnterScript).not.toHaveBeenCalled()
    expect(chatStore.state.value.key).toBe('b')
  })
})

describe('the notification bus runs a pushed on-enter script once, globally', () => {
  let onEnterActions
  let chatClient
  let bus

  beforeEach(async () => {
    vi.resetModules()
    onEnterActions = await import('../src/onEnterActions.js')
    chatClient = await import('../src/chatClient.js')
    bus = await import('../src/notificationBus.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  function pushedFrame() {
    // What chatClient's single handler received: the bus registers it
    // the first time anyone subscribes.
    expect(chatClient.onNotification).toHaveBeenCalledTimes(1)
    return chatClient.onNotification.mock.calls[0][0]
  }

  it('runs the script of a frame carrying only "on-enter" (an OnEnterTask that ran server-side)', () => {
    const seen = []
    bus.subscribeToStateNotifications((frame) => seen.push(frame))

    pushedFrame()({ project_name: undefined, state: undefined, 'on-enter': "notify('Nice!', 'You reached **state B**.')" })

    expect(onEnterActions.runOnEnterScript).toHaveBeenCalledTimes(1)
    expect(onEnterActions.runOnEnterScript).toHaveBeenCalledWith("notify('Nice!', 'You reached **state B**.')")
    expect(seen).toEqual([])  // no state: nothing for the stores
  })

  it('runs the script once however many stores subscribed, and hands the state to each', () => {
    const a = []
    const b = []
    bus.subscribeToStateNotifications((frame) => a.push(frame))
    bus.subscribeToStateNotifications((frame) => b.push(frame))

    pushedFrame()({ project_name: 'proj', state: { key: 'x' }, 'on-enter': 'celebrate()' })

    expect(onEnterActions.runOnEnterScript).toHaveBeenCalledTimes(1)
    expect(a).toEqual([{ project_name: 'proj', state: { key: 'x' } }])
    expect(b).toEqual([{ project_name: 'proj', state: { key: 'x' } }])
  })

  it('a live chat store applies a pushed state only when it is about its own project', async () => {
    const chatStore = await import('../src/chatStore.js')
    chatStore.currentProjectId.value = 'proj'

    pushedFrame()({ project_name: 'other', state: { key: 'x', actions: [] } })
    expect(chatStore.state.value?.key).not.toBe('x')

    pushedFrame()({ project_name: 'proj', state: { key: 'x', actions: [] } })
    expect(chatStore.state.value.key).toBe('x')
  })
})

describe('submitMessage correlates ids directly, never through result.reply', () => {
  let chatStore
  let chatClient

  beforeEach(async () => {
    vi.resetModules()
    chatStore = await import('../src/chatStore.js')
    chatClient = await import('../src/chatClient.js')
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('stamps the local user bubble with the backend-assigned user_message_id', async () => {
    chatClient.sendMessage.mockResolvedValue({
      reply: [],
      user_message_id: 42,
      assistant_message_id: 5,
      state: { key: 'a', ui_label: 'A', actions: [] },
      'on-enter': null,
      session_id: 1
    })

    await chatStore.handleSend('hello')

    const userMessage = chatStore.messages.value.find((m) => m.role === 'user')
    expect(userMessage.messageId).toBe(42)
  })

  it('applies the bot-supplied reaction to the local user bubble live, via a replaced object (not a direct mutation)', async () => {
    // Regression: an earlier version mutated the raw `message` object this
    // closure holds directly (message.reaction = ...) instead of replacing
    // its slot in messages.value — that bypasses Vue's reactive proxy
    // entirely, so the bubble never re-rendered until something else (e.g.
    // a full reload) rebuilt messages.value from scratch.
    chatClient.sendMessage.mockResolvedValue({
      reply: [],
      user_message_id: 42,
      user_message_reaction: 'listening',
      assistant_message_id: 5,
      state: { key: 'a', ui_label: 'A', actions: [] },
      'on-enter': null,
      session_id: 1
    })

    await chatStore.handleSend('hello')

    const userMessage = chatStore.messages.value.find((m) => m.role === 'user')
    expect(userMessage.reaction).toBe('listening')
  })

  it('sets statusText on the streaming bubble on a tool_call event, and clears it on tool_result', async () => {
    let duringCall = null
    let afterResult = null
    chatClient.sendMessage.mockImplementation(async (text, sessionId, { onStatus }) => {
      onStatus('Searching Flights…')
      duringCall = chatStore.messages.value.find((m) => m.role === 'assistant')?.statusText
      onStatus('')
      afterResult = chatStore.messages.value.find((m) => m.role === 'assistant')?.statusText
      return {
        reply: [], user_message_id: 42, assistant_message_id: 5,
        state: { key: 'a', ui_label: 'A', actions: [] }, 'on-enter': null, session_id: 1
      }
    })

    await chatStore.handleSend('hello')

    expect(duringCall).toBe('Searching Flights…')
    expect(afterResult).toBe('')
  })

  it('clears any stale reaction on the local user bubble when this turn carries none', async () => {
    chatClient.sendMessage.mockResolvedValue({
      reply: [],
      user_message_id: 42,
      assistant_message_id: 5,
      state: { key: 'a', ui_label: 'A', actions: [] },
      'on-enter': null,
      session_id: 1
    })

    await chatStore.handleSend('hello')

    const userMessage = chatStore.messages.value.find((m) => m.role === 'user')
    expect(userMessage.reaction).toBe(null)
  })

  it('stamps the streaming bubble with the backend-assigned assistant_message_id', async () => {
    // Regression test: chat_service.py's process_turn never populates
    // "reply" with message objects (OutVariables.messages stays [], see
    // backend tests/test_chat_service_evaluation_points.py) — the
    // streaming bubble must be correlated directly via
    // assistant_message_id, not by matching into result.reply (which is
    // always empty for a real turn, so that path never actually ran and
    // the bubble's own messageId stayed null forever — losing the
    // Inspector's message-keyed lookups and the timeline's own transition
    // positioning for every single turn).
    chatClient.sendMessage.mockResolvedValue({
      reply: [],
      user_message_id: 42,
      assistant_message_id: 11,
      state: { key: 'a', ui_label: 'A', actions: [] },
      'on-enter': null,
      session_id: 1
    })

    await chatStore.handleSend('hello')

    const assistantMessages = chatStore.messages.value.filter((m) => m.role === 'assistant')
    expect(assistantMessages).toHaveLength(1)
    expect(assistantMessages[0].messageId).toBe(11)
  })

  it('drops the empty streaming placeholder when no live reply was generated this turn', async () => {
    // A pre-turn transition can move to a state that doesn't chat at all
    // (see TurnProcessor.process's own early exit) — assistant_message_id
    // is null then, nothing was ever streamed into the placeholder.
    chatClient.sendMessage.mockResolvedValue({
      reply: [],
      user_message_id: 42,
      assistant_message_id: null,
      state: { key: 'a', ui_label: 'A', actions: [] },
      'on-enter': null,
      session_id: 1
    })

    await chatStore.handleSend('hello')

    const assistantMessages = chatStore.messages.value.filter((m) => m.role === 'assistant')
    expect(assistantMessages).toHaveLength(0)
  })
})

describe('submitMessage keeps every turn correctly ordered against real buildTimeline', () => {
  let chatStore
  let chatClient

  beforeEach(async () => {
    vi.resetModules()
    vi.useFakeTimers()
    chatStore = await import('../src/chatStore.js')
    chatClient = await import('../src/chatClient.js')
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('positions a second turn\'s own transition after its own user message, not after the assistant reply', async () => {
    // Regression test: the streaming assistant bubble's own local
    // `timestamp` used to be stamped once at placeholder-creation time
    // (submitMessage's own push, essentially the same instant as the
    // triggering user message) and never updated afterward. A second
    // turn sent while that stale timestamp was still fresh could then
    // collide with (or trail only slightly behind) the next user
    // message's own timestamp — and buildTimeline's own tie-break (a
    // message always sorts before a same-effective-moment transition)
    // then pushed the transition past bubbles it should have preceded.
    // Reproduced directly against a live "before" mode session
    // (autotracking_on_ai_message=False): the first transition rendered
    // fine, the second landed after the assistant's reply instead of
    // right after the user's own message.
    chatClient.sendMessage.mockImplementationOnce(async () => {
      vi.advanceTimersByTime(2000) // the AI reply genuinely takes real time
      return {
        reply: [],
        user_message_id: 3,
        assistant_message_id: 4,
        state: { key: 'Contemplation', ui_label: 'Contemplation', actions: [] },
        state_changed: true,
        'on-enter': null,
        session_id: 1
      }
    })
    await chatStore.handleSend('turn 1')
    vi.advanceTimersByTime(20000) // the user takes real time to type turn 2

    chatClient.sendMessage.mockImplementationOnce(async () => {
      vi.advanceTimersByTime(2000)
      return {
        reply: [],
        user_message_id: 5,
        assistant_message_id: 6,
        state: { key: 'Preparation', ui_label: 'Preparation', actions: [] },
        state_changed: true,
        'on-enter': null,
        session_id: 1
      }
    })
    await chatStore.handleSend('turn 2')

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
