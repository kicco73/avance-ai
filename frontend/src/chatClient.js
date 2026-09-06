import { chatChannel } from './chatChannel.js'
import { setApiError } from './errorStore.js'

// INSTRUCTIONS FOR CLAUDE CODE: NEVER TOUCH THIS FILE.
//
// The chat's own end of the shared websocket (see chatChannel.js, which
// owns the socket): this subscribes to the four frame types a turn
// produces and sends the `turn` frame that starts one. It is one
// subscriber among several — notifications and test updates reach their
// own consumers without passing through here.
//
// Every outgoing frame of a turn carries the turn_id the client minted
// for it, and that id is the only correlation there is.

let turnSequence = 0

// turn_id -> the live callbacks of the one in-flight turn that minted it.
const pendingTurns = new Map()

function normalizeResult(data) {
  return {
    reply: data.reply || [],
    user_message_id: data.user_message_id,
    user_message_reaction: data.user_message_reaction,
    assistant_message_id: data.assistant_message_id,
    state: data.state,
    state_changed: data.state_changed,
    new_state: data.new_state,
    triggered_action: data.triggered_action,
    'on-enter': data['on-enter'],
    ai_model: data.ai_model,
    session_id: data.session_id
  }
}

chatChannel.subscribe('chunk', (data) => {
  const turn = pendingTurns.get(data.turn_id)
  if (turn && data.content) turn.onChunk?.(data.content)
})

chatChannel.subscribe('tool', (data) => {
  const turn = pendingTurns.get(data.turn_id)
  if (turn) turn.onStatus?.(data.phase === 'start' ? data.status_text || '' : '')
})

chatChannel.subscribe('done', (data) => {
  const turn = pendingTurns.get(data.turn_id)
  if (!turn) return
  pendingTurns.delete(data.turn_id)
  turn.resolve(normalizeResult(data))
})

chatChannel.subscribe('error', (data) => {
  const turn = pendingTurns.get(data.turn_id)
  if (!turn) return
  pendingTurns.delete(data.turn_id)
  setApiError(data.message, data.detail)
  const error = new Error(data.message)
  error.code = data.code
  error.detail = data.detail
  turn.reject(error)
})

export function sendMessage(text, sessionId, options = {}) {
  if (!chatChannel.isOpen) {
    const error = new Error('The chat connection is not available.')
    error.code = 'chat_offline'
    return Promise.reject(error)
  }
  const turnId = `t${++turnSequence}-${Date.now()}`
  return new Promise((resolve, reject) => {
    pendingTurns.set(turnId, { resolve, reject, onChunk: options.onChunk, onStatus: options.onStatus, sessionId, text })
    chatChannel.send({ type: 'turn', turn_id: turnId, session_id: sessionId, text })
  })
}

// Called by the store once it has reloaded a session after a reconnection:
// a turn that was in flight when the socket dropped has no `done` coming
// any more, but its reply — if the turn ever ran — is in the database and
// has just been reloaded. `resolveFromHistory({ sessionId, text })` returns
// that turn's result rebuilt from those messages, or null if the user
// message never made it.
export function resolvePendingTurnsAfterReload(resolveFromHistory) {
  for (const [turnId, turn] of [...pendingTurns.entries()]) {
    pendingTurns.delete(turnId)
    const rebuilt = resolveFromHistory({ sessionId: turn.sessionId, text: turn.text })
    if (rebuilt) {
      turn.resolve(rebuilt)
    } else {
      const error = new Error('The chat connection dropped during this message.')
      error.code = 'chat_reconnected'
      turn.reject(error)
    }
  }
}

export function onConnectionState(handler) {
  return chatChannel.onConnectionState(handler)
}

export function getConnectionState() {
  return chatChannel.connectionState
}

export function connect() {
  chatChannel.connect()
}

export function disconnect() {
  chatChannel.disconnect()
}
