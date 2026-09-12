import { busChannel } from './busChannel.js'

// INSTRUCTIONS FOR CLAUDE CODE: NEVER TOUCH THIS FILE.
//
// The chat's own end of the shared websocket (see busChannel.js, which
// owns the socket): what a person does goes out from here — what they
// said, and which button they pressed. Nothing comes back through this
// file and nothing is awaited: what the system publishes about an
// exchange is observed by whoever is showing the chat (see
// chatExchange.js), one subscriber among several.

export function sendMessage(text, sessionId) {
  return busChannel.send({ type: 'input.text', session_id: sessionId, text })
}

// A conversation has just been opened. Whether the automaton has
// anything to say before anybody says anything is its own business —
// what comes back, if anything, is an ordinary message.
export function sendSessionOpened(sessionId) {
  return busChannel.send({ type: 'session.new', session_id: sessionId })
}

export function sendButton(id, sessionId) {
  return busChannel.send({ type: 'input.button', session_id: sessionId, id })
}

export function onConnectionState(handler) {
  return busChannel.onConnectionState(handler)
}

export function getConnectionState() {
  return busChannel.connectionState
}

export function connect() {
  busChannel.connect()
}

export function disconnect() {
  busChannel.disconnect()
}
