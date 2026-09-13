// The tool status line ("Searching …") must stay readable even when the
// tool answers in a few milliseconds: the store keeps it for at least
// TOOL_STATUS_MIN_MS from the moment it was shown (see toolStatusHold.js),
// and the answer itself never cuts it short. A result that arrives after
// the minimum clears it at once.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TOOL_STATUS_MIN_MS } from '../src/toolStatusHold.js'

vi.mock('../src/busChannel.js', () => import('./fakeBus.js'))
vi.mock('../src/taskActions.js', () => ({ runTaskScript: vi.fn() }))
vi.mock('../src/api.js', () => ({
  getSessions: vi.fn(),
  getAiModels: vi.fn(),
  getHistory: vi.fn().mockResolvedValue([]),
  getActuators: vi.fn(),
  putActuators: vi.fn(),
  postTruncateSession: vi.fn(),
  deleteSession: vi.fn(),
}))

const STATUS = 'Searching Flights…'

function assistantStatus(chatStore) {
  return chatStore.messages.value.find((m) => m.role === 'assistant')?.statusText
}

describe('tool status minimum display time', () => {
  let chatStore
  let deliver

  beforeEach(async () => {
    vi.useFakeTimers()
    vi.resetModules()
    const bus = await import('./fakeBus.js')
    bus.resetFakeBus()
    deliver = bus.deliver
    chatStore = await import('../src/chatStore.js')
    chatStore.currentSessionId.value = 1
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  function toolStarts() {
    deliver({ type: 'output.tool', session_id: 1, phase: 'start', status_text: STATUS })
  }

  function toolAnswers() {
    deliver({ type: 'output.tool', session_id: 1, phase: 'result' })
  }

  function said() {
    deliver({ type: 'state.buttons', session_id: 1, actions: [] })
    deliver({ type: 'output.text', session_id: 1, assistant_message_id: 51, text: 'Found it.', timestamp: 't' })
  }

  it('keeps the status shown when the result lands after 10 ms, and clears it once TOOL_STATUS_MIN_MS is up', async () => {
    await chatStore.handleSend('where is my flight?')
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    toolStarts()
    await vi.advanceTimersByTimeAsync(10)
    toolAnswers()
    said()

    expect(assistantStatus(chatStore)).toBe(STATUS)
    await vi.advanceTimersByTimeAsync(1000 - 10)
    expect(assistantStatus(chatStore)).toBe(STATUS)
    await vi.advanceTimersByTimeAsync(600)
    expect(assistantStatus(chatStore)).toBe('')
  })

  it('clears the status at once when the result lands after the minimum has already passed', async () => {
    await chatStore.handleSend('where is my flight?')
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    toolStarts()
    await vi.advanceTimersByTimeAsync(2000)
    toolAnswers()

    expect(assistantStatus(chatStore)).toBe('')

    said()

    expect(assistantStatus(chatStore)).toBe('')
  })

  it('does not let the answer cut the minimum short', async () => {
    await chatStore.handleSend('where is my flight?')
    deliver({ type: 'output.text_stream', session_id: 1, text: '' })
    toolStarts()
    toolAnswers()
    said()

    expect(assistantStatus(chatStore)).toBe(STATUS)
    await vi.advanceTimersByTimeAsync(TOOL_STATUS_MIN_MS)
    expect(assistantStatus(chatStore)).toBe('')
  })
})
