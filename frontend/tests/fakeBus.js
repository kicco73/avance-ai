import { vi } from 'vitest'

const handlers = (globalThis.__fakeBusHandlers ??= new Map())

export const busChannel = (globalThis.__fakeBusChannel ??= {
  isOpen: true,
  connectionState: 'open',
  subscribe(type, handler) {
    if (!handlers.has(type)) handlers.set(type, new Set())
    handlers.get(type).add(handler)
    return () => handlers.get(type)?.delete(handler)
  },
  send: vi.fn(() => true),
  onConnectionState: vi.fn(() => () => {}),
  connect: vi.fn(),
  disconnect: vi.fn()
})

export function deliver(frame) {
  for (const handler of [...(handlers.get(frame.type) ?? [])]) handler(frame)
}

export function deliverExchange({ sessionId = 1, chunks = [], actions = [], answer }) {
  deliver({ type: 'output.text_stream', session_id: sessionId, text: '' })
  for (const text of chunks) deliver({ type: 'output.text_stream', session_id: sessionId, text })
  deliver({ type: 'state.buttons', session_id: sessionId, actions })
  deliver({
    type: 'output.text', session_id: sessionId,
    assistant_message_id: answer.id, text: answer.content, audio_text: answer.audio_text ?? null
  })
}

export function resetFakeBus() {
  handlers.clear()
  busChannel.send.mockClear()
}

export function deliverEntered({
  sessionId = 1, projectId = 'proj', sessionType = 'live',
  messages = [], state = null, services = {}, actions = [], current = true, audio = false,
  channel = 'webchat'
}) {
  deliver({
    type: 'session.info', session_id: sessionId, project_id: projectId, session_type: sessionType,
    state, services, audio, current, channel
  })
  deliver({ type: 'session.messages', session_id: sessionId, messages })
  deliver({ type: 'state.buttons', session_id: sessionId, actions })
}
