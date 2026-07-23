import { createChatSocket, postChatMessage } from './api.js'
import { setApiError } from './errorStore.js'

// Raised only when the websocket never actually opens (or closes without
// ever having opened) — the signal sendMessage() uses to fall back to
// REST silently. Anything else (a post-open drop, a real 'error' frame)
// is a genuine turn failure and must NOT trigger a fallback.
class WebSocketUnavailableError extends Error {}

// Once true, every subsequent sendMessage() in this session goes straight
// to REST — never retried until a full page reload resets this module.
let websocketUnavailable = false

let socket = null
let pendingTurn = null // { resolve, reject, onStatus } for the in-flight turn, if any

function normalizeResult(data) {
  return {
    reply: data.reply,
    state: data.state,
    state_changed: data.state_changed,
    new_state: data.new_state,
    triggered_action: data.triggered_action
  }
}

function handleSocketMessage(event) {
  const data = JSON.parse(event.data)

  if (data.type === 'retrying') {
    const seconds = Math.max(0, Math.ceil(data.retry_in ?? 0))
    pendingTurn?.onStatus?.(`Service unavailable, retrying (${data.attempt}/${data.max_attempts}) in ${seconds}s...`)
    return
  }

  if (data.type === 'error') {
    setApiError(data.error.message, data.error.detail)
    pendingTurn?.reject(new Error(data.error.message))
    pendingTurn = null
    return
  }

  if (!pendingTurn) return
  const { resolve } = pendingTurn
  pendingTurn = null
  resolve(normalizeResult(data)) // data.type === 'done'
}

function openSocket() {
  return new Promise((resolve, reject) => {
    const ws = createChatSocket()
    let opened = false
    ws.onopen = () => {
      opened = true
      resolve(ws)
    }
    ws.onmessage = handleSocketMessage
    ws.onerror = () => {} // onclose always follows for a WebSocket; it decides what happens next
    ws.onclose = () => {
      if (socket === ws) socket = null
      if (!opened) {
        // Never actually connected — sendMessage() falls back to REST
        // silently for this, so no error surfaces from here.
        reject(new WebSocketUnavailableError('Unable to connect to the chat service.'))
        return
      }
      if (pendingTurn) {
        const err = new Error('Chat connection closed.')
        setApiError(err.message)
        pendingTurn.reject(err)
        pendingTurn = null
      }
    }
    socket = ws
  })
}

async function ensureSocket() {
  if (socket && socket.readyState === WebSocket.OPEN) return socket
  socket = await openSocket()
  return socket
}

async function sendViaWebsocket(text, onStatus) {
  const ws = await ensureSocket()
  return new Promise((resolve, reject) => {
    pendingTurn = { resolve, reject, onStatus }
    ws.send(JSON.stringify({ message: text }))
  })
}

async function sendViaRest(text) {
  const data = await postChatMessage(text) // apiFetch already surfaces errors uniformly
  return normalizeResult(data)
}

// The one thing the chat UI calls to send a message — it never knows
// which transport actually ran. Tries the websocket first; the moment a
// connection attempt fails to ever open, permanently falls back to REST
// for the rest of the session (until a page reload resets this module).
// `onStatus`, if given, is called with retry-progress text — REST never
// calls it, since retries there are silent server-side (see api.js).
export async function sendMessage(text, { onStatus } = {}) {
  if (!websocketUnavailable) {
    try {
      return await sendViaWebsocket(text, onStatus)
    } catch (err) {
      if (!(err instanceof WebSocketUnavailableError)) throw err
      websocketUnavailable = true
    }
  }
  return sendViaRest(text)
}

export function disconnect() {
  socket?.close()
  socket = null
}
