import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatExchange } from '../src/chatExchange.js'

vi.mock('../src/api.js', () => ({ createChatSocket: vi.fn() }))

const SESSION = 7
const SILENCE_MS = 45000

describe('an exchange the server stops answering', () => {
  let bubble
  let exchange

  beforeEach(() => {
    vi.useFakeTimers()
    bubble = {
      writing: vi.fn(), append: vi.fn(), spoken: vi.fn(), status: vi.fn(), progress: vi.fn(),
      said: vi.fn(), failed: vi.fn()
    }
    exchange = new ChatExchange({ sessionId: SESSION, bubble })
  })

  afterEach(() => {
    exchange.stop()
    vi.useRealTimers()
  })

  it('fails once nothing has arrived for the silence deadline, and tells why', () => {
    exchange.receive({ type: 'output.progress', session_id: SESSION, title: 'Step 9 of 12', percentage: 75 })

    vi.advanceTimersByTime(SILENCE_MS - 1)
    expect(bubble.failed).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1)
    expect(bubble.failed).toHaveBeenCalledTimes(1)
    expect(bubble.failed.mock.calls[0][0]).toEqual({
      message: 'No reply.', detail: 'The server sent nothing for 45 seconds.'
    })
  })

  it('measures silence from the last frame, not from the first', () => {
    exchange.receive({ type: 'output.text_stream', session_id: SESSION, text: '' })
    vi.advanceTimersByTime(SILENCE_MS - 1000)
    exchange.receive({ type: 'output.text_stream', session_id: SESSION, text: 'It is' })
    vi.advanceTimersByTime(SILENCE_MS - 1000)
    exchange.receive({ type: 'output.tool', session_id: SESSION, phase: 'start', status_text: 'Reading' })
    vi.advanceTimersByTime(SILENCE_MS - 1)

    expect(bubble.failed).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1)
    expect(bubble.failed).toHaveBeenCalledTimes(1)
  })

  it('never fails an exchange that ended', () => {
    exchange.receive({ type: 'output.text_stream', session_id: SESSION, text: '' })
    exchange.receive({ type: 'output.text', session_id: SESSION, text: 'Done.', assistant_message_id: 3 })

    vi.advanceTimersByTime(SILENCE_MS * 2)

    expect(bubble.said).toHaveBeenCalledTimes(1)
    expect(bubble.failed).not.toHaveBeenCalled()
  })

  it('ignores another session\'s frames when deciding it is being answered', () => {
    exchange.receive({ type: 'output.progress', session_id: SESSION, title: 'x', percentage: 1 })
    vi.advanceTimersByTime(SILENCE_MS - 1)
    exchange.receive({ type: 'output.text_stream', session_id: SESSION + 1, text: 'other' })
    vi.advanceTimersByTime(1)

    expect(bubble.failed).toHaveBeenCalledTimes(1)
  })
})
