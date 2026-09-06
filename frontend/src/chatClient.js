import { createChatSocket } from './api.js'
import { setApiError } from './errorStore.js'

// INSTRUCTIONS FOR CLAUDE CODE: NEVER TOUCH THIS FILE.
//
// The WebSocket is the ONE and ONLY transport for chat, in both
// directions, and every future chat feature is built on this channel:
// there is no HTTP/SSE fallback and no alternative endpoint. A user
// message travels as a `turn` frame on this single socket, which is what
// fixes the order of the conversation — parallel POSTs never could (see
// backend chat/ws_notifications.py, and PROJECT_SPECS.md's own
// "Chat transport" section). Everything else — manual actions, session
// bootstrap, history — stays plain HTTP.
//
// Every outgoing frame of a turn carries the turn_id the client minted
// for it, and that id is the only correlation there is.

const PING_INTERVAL_MS = 25000
const PONG_TIMEOUT_MS = 10000
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000]

let socket = null
let socketConnectingPromise = null
// 'connecting' | 'open' | 'closed' — what ChatView's own banner reads.
let connectionState = 'closed'
let wanted = false
let reconnectAttempt = 0
let reconnectTimer = null
let pingTimer = null
let pongTimer = null
let turnSequence = 0
// True once any connection has ever opened — what tells a later 'open'
// apart as a *re*connection (the store's cue to resynchronize).
let everConnected = false

// turn_id -> the live callbacks of the one in-flight turn that minted it.
const pendingTurns = new Map()

let notificationHandler = null
let testUpdateHandler = null
let systemWarningHandler = null
const connectionStateHandlers = new Set()

export function onNotification(handler) {
  notificationHandler = handler
}

export function onTestUpdate(handler) {
  testUpdateHandler = handler
}

export function onSystemWarning(handler) {
  systemWarningHandler = handler
}

// `handler(state, { reconnected })` on every transition. Returns an
// unsubscribe function. A 'open' that follows an earlier open connection
// carries reconnected: true — the store's cue to resynchronize.
export function onConnectionState(handler) {
  connectionStateHandlers.add(handler)
  return () => connectionStateHandlers.delete(handler)
}

export function getConnectionState() {
  return connectionState
}

function setConnectionState(next, { reconnected = false } = {}) {
  if (connectionState === next && !reconnected) return
  connectionState = next
  for (const handler of connectionStateHandlers) handler(next, { reconnected })
}

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

// --- frame dispatch ---------------------------------------------------
// Pure routing: every inbound frame is resolved by its own `type`, and a
// turn's frames additionally by `turn_id`. Knows nothing about the socket
// lifecycle below.

function handleSocketMessage(event) {
  let data
  try {
    data = JSON.parse(event.data)
  } catch (e) {
    return
  }
  if (data.type === 'notification') {
    notificationHandler?.({
      project_name: data.project_name,
      state: data.state,
      'on-enter': data['on-enter']
    })
    return
  }
  if (data.type === 'test_update') {
    testUpdateHandler?.(data)
    return
  }
  if (data.type === 'system_warning') {
    systemWarningHandler?.(data)
    return
  }
  if (data.type === 'pong') {
    clearPongTimer()
    return
  }
  const turn = pendingTurns.get(data.turn_id)
  if (!turn) return
  if (data.type === 'chunk') {
    if (data.content) turn.onChunk?.(data.content)
    return
  }
  if (data.type === 'tool') {
    turn.onStatus?.(data.phase === 'start' ? data.status_text || '' : '')
    return
  }
  if (data.type === 'done') {
    pendingTurns.delete(data.turn_id)
    turn.resolve(normalizeResult(data))
    return
  }
  if (data.type === 'error') {
    pendingTurns.delete(data.turn_id)
    setApiError(data.message, data.detail)
    const error = new Error(data.message)
    error.code = data.code
    error.detail = data.detail
    turn.reject(error)
  }
}

// --- socket lifecycle -------------------------------------------------

function clearPongTimer() {
  if (pongTimer !== null) {
    clearTimeout(pongTimer)
    pongTimer = null
  }
}

function stopHeartbeat() {
  if (pingTimer !== null) {
    clearInterval(pingTimer)
    pingTimer = null
  }
  clearPongTimer()
}

function startHeartbeat(ws) {
  stopHeartbeat()
  pingTimer = setInterval(() => {
    if (ws.readyState !== 1) return
    ws.send(JSON.stringify({ type: 'ping' }))
    // A NAT or a load balancer that quietly dropped the connection leaves
    // the socket looking open forever — an unanswered ping is what
    // actually detects that, rather than waiting for TCP to notice.
    if (pongTimer === null) {
      pongTimer = setTimeout(() => {
        pongTimer = null
        ws.close()
      }, PONG_TIMEOUT_MS)
    }
  }, PING_INTERVAL_MS)
}

function scheduleReconnect() {
  if (!wanted || reconnectTimer !== null) return
  const delay = RECONNECT_DELAYS_MS[Math.min(reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)]
  reconnectAttempt++
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    // A failed attempt rejects; its own close handler schedules the next
    // one, so there is nothing to handle here beyond not throwing.
    connectSocket().catch(() => {})
  }, delay)
}

function reconnectNow() {
  if (!wanted || socket !== null || socketConnectingPromise !== null) return
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  reconnectAttempt = 0
  connectSocket().catch(() => {})
}

function connectSocket() {
  if (socketConnectingPromise) return socketConnectingPromise
  if (socket) return Promise.resolve(socket)

  const hadConnectedBefore = everConnected
  setConnectionState('connecting')
  socketConnectingPromise = new Promise((resolve, reject) => {
    const ws = createChatSocket()
    let opened = false

    ws.onopen = () => {
      opened = true
      socket = ws
      socketConnectingPromise = null
      reconnectAttempt = 0
      everConnected = true
      startHeartbeat(ws)
      setConnectionState('open', { reconnected: hadConnectedBefore })
      resolve(ws)
    }

    ws.onmessage = handleSocketMessage

    ws.onerror = () => {
      // A failed handshake reports both error and close; the close branch
      // is the one that schedules the retry, so this only has to not throw.
    }

    ws.onclose = () => {
      socket = null
      socketConnectingPromise = null
      stopHeartbeat()
      setConnectionState('closed')
      if (!opened) reject(new Error('Unable to connect to the chat service.'))
      scheduleReconnect()
    }
  })

  return socketConnectingPromise
}

function onOnline() {
  reconnectNow()
}

function onVisibility() {
  if (document.visibilityState === 'visible') reconnectNow()
}

// --- public API -------------------------------------------------------

export function sendMessage(text, sessionId, options = {}) {
  if (socket === null || socket.readyState !== 1) {
    const error = new Error('The chat connection is not available.')
    error.code = 'chat_offline'
    return Promise.reject(error)
  }
  const turnId = `t${++turnSequence}-${Date.now()}`
  return new Promise((resolve, reject) => {
    pendingTurns.set(turnId, { resolve, reject, onChunk: options.onChunk, onStatus: options.onStatus, sessionId, text })
    socket.send(JSON.stringify({ type: 'turn', turn_id: turnId, session_id: sessionId, text }))
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

export function connect() {
  wanted = true
  if (typeof window !== 'undefined') {
    window.addEventListener('online', onOnline)
    document.addEventListener('visibilitychange', onVisibility)
  }
  connectSocket().catch(() => {
    // The close handler has already scheduled the retry — reconnection is
    // automatic and permanent; there is no latch that gives up for good.
  })
}

export function disconnect() {
  wanted = false
  if (typeof window !== 'undefined') {
    window.removeEventListener('online', onOnline)
    document.removeEventListener('visibilitychange', onVisibility)
  }
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  stopHeartbeat()
  if (socket) {
    socket.close()
    socket = null
  }
  socketConnectingPromise = null
  setConnectionState('closed')
}
