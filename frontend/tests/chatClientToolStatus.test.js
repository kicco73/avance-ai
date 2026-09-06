// Regression for chatClient.js's own readSseTurnStream: a 'tool' SSE
// event's own status_text must reach the caller's onStatus verbatim on
// phase "start", and any other phase must clear it (onStatus('')) —
// see ai_service.py's own tool-call loop and chatStoreFactory.js's own
// per-bubble statusText wiring this feeds.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api.js', () => ({
  createChatSocket: vi.fn(),
  postChatMessage: vi.fn()
}))
vi.mock('../src/errorStore.js', () => ({ setApiError: vi.fn() }))

function fakeSseResponse(events) {
  const body = events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('')
  const chunk = new TextEncoder().encode(body)
  let sent = false
  return {
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined }
          sent = true
          return { done: false, value: chunk }
        }
      })
    }
  }
}

describe('sendMessage forwards the tool event as onStatus', () => {
  let chatClient
  let api

  beforeEach(async () => {
    vi.resetModules()
    chatClient = await import('../src/chatClient.js')
    api = await import('../src/api.js')

    api.createChatSocket.mockImplementation(() => {
      const ws = { onopen: null, onmessage: null, onerror: null, onclose: null }
      queueMicrotask(() => ws.onclose?.())
      return ws
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('calls onStatus with status_text on phase "start", then with empty string on phase "result"', async () => {
    api.postChatMessage.mockResolvedValue(fakeSseResponse([
      ['tool', { phase: 'start', status_text: 'Searching Flights…' }],
      ['tool', { phase: 'result' }],
      ['done', { reply: [], session_id: 1 }]
    ]))
    const statuses = []

    await chatClient.sendMessage('hi', 1, { onStatus: (text) => statuses.push(text) })

    expect(statuses).toEqual(['Searching Flights…', ''])
  })

  it('never calls onStatus when no tool call happens this turn', async () => {
    api.postChatMessage.mockResolvedValue(fakeSseResponse([
      ['chunk', { content: 'Hi!' }],
      ['done', { reply: [], session_id: 1 }]
    ]))
    const statuses = []

    await chatClient.sendMessage('hi', 1, { onStatus: (text) => statuses.push(text) })

    expect(statuses).toEqual([])
  })
})
