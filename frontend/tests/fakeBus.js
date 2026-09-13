import { vi } from 'vitest'

// The socket as the app sees it, with a way to publish into it. A test
// drives a chat the way the system does: it sends, and then the frames
// arrive — nothing is awaited and nothing is stubbed in between.
// One channel for every copy of this module. `vi.resetModules()` gives
// the store a fresh copy and the test another: two copies means the test
// publishes where nobody is listening, and reads a `send` nobody called.
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

// One whole exchange, in the order the system publishes it (see backend
// docs/BUS.md): the pieces as they are written, then what can be done
// next, then the answer — which is what ends it.
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

// What the server answers `session.enter` with, in its order (see
// backend docs/BUS.md): which conversation this is, what was said in it,
// what can be done next. A store that has just called `loadMessages` is
// waiting for exactly these three, and has no session until they arrive.
export function deliverEntered({
  sessionId = 1, projectId = 'proj', sessionType = 'live',
  messages = [], state = null, services = {}, actions = [], current = true, audio = false
}) {
  deliver({
    type: 'session.info', session_id: sessionId, project_id: projectId, session_type: sessionType,
    state, services, audio, current, channel: 'webchat'
  })
  deliver({ type: 'session.messages', session_id: sessionId, messages })
  deliver({ type: 'state.buttons', session_id: sessionId, actions })
}
