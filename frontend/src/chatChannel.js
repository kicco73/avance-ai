import { createChatSocket } from './api.js'

// The one websocket per user, both directions, and the only thing that
// owns it: connection lifecycle (connect/reconnect/heartbeat) plus pure
// routing of every inbound frame to whoever subscribed to its `type`.
// It knows nothing about what any frame means — chat turns correlate
// themselves by turn_id in chatClient.js, notifications fan out in
// notificationBus.js, and both are ordinary subscribers here.
//
// The WebSocket is the ONE and ONLY transport for chat, in both
// directions, and every future chat feature is built on this channel:
// there is no HTTP/SSE fallback and no alternative endpoint. A user
// message travels as a `turn` frame on this single socket, which is what
// fixes the order of the conversation — parallel POSTs never could (see
// backend chat/ws_notifications.py, and PROJECT_SPECS.md's own
// "Chat transport" section). Everything else — manual actions, session
// bootstrap, history — stays plain HTTP.

const PING_INTERVAL_MS = 25000
const PONG_TIMEOUT_MS = 10000
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000]

class ChatChannel {
  constructor() {
    this._socket = null
    this._connectingPromise = null
    // 'connecting' | 'open' | 'closed' — what ChatView's own banner reads.
    this._connectionState = 'closed'
    this._wanted = false
    this._reconnectAttempt = 0
    this._reconnectTimer = null
    this._pingTimer = null
    this._pongTimer = null
    // True once any connection has ever opened — what tells a later 'open'
    // apart as a *re*connection (the store's cue to resynchronize).
    this._everConnected = false
    this._subscribers = new Map()
    this._connectionStateHandlers = new Set()
    this._onOnline = () => this._reconnectNow()
    this._onVisibility = () => {
      if (document.visibilityState === 'visible') this._reconnectNow()
    }
  }

  // `handler(frame)` for every inbound frame of this `type`, as many
  // subscribers per type as ask for it. Returns an unsubscribe function.
  subscribe(type, handler) {
    let handlers = this._subscribers.get(type)
    if (handlers === undefined) {
      handlers = new Set()
      this._subscribers.set(type, handlers)
    }
    handlers.add(handler)
    return () => handlers.delete(handler)
  }

  // `handler(state, { reconnected })` on every transition. Returns an
  // unsubscribe function. A 'open' that follows an earlier open connection
  // carries reconnected: true — the store's cue to resynchronize.
  onConnectionState(handler) {
    this._connectionStateHandlers.add(handler)
    return () => this._connectionStateHandlers.delete(handler)
  }

  get connectionState() {
    return this._connectionState
  }

  get isOpen() {
    return this._socket !== null && this._socket.readyState === 1
  }

  // Puts one frame on the wire. False means there was no open socket to
  // put it on; the caller decides what that means for its own frame.
  send(payload) {
    if (!this.isOpen) return false
    this._socket.send(JSON.stringify(payload))
    return true
  }

  connect() {
    this._wanted = true
    if (typeof window !== 'undefined') {
      window.addEventListener('online', this._onOnline)
      document.addEventListener('visibilitychange', this._onVisibility)
    }
    this._connectSocket().catch(() => {
      // The close handler has already scheduled the retry — reconnection is
      // automatic and permanent; there is no latch that gives up for good.
    })
  }

  disconnect() {
    this._wanted = false
    if (typeof window !== 'undefined') {
      window.removeEventListener('online', this._onOnline)
      document.removeEventListener('visibilitychange', this._onVisibility)
    }
    if (this._reconnectTimer !== null) {
      clearTimeout(this._reconnectTimer)
      this._reconnectTimer = null
    }
    this._stopHeartbeat()
    if (this._socket) {
      this._socket.close()
      this._socket = null
    }
    this._connectingPromise = null
    this._setConnectionState('closed')
  }

  _setConnectionState(next, { reconnected = false } = {}) {
    if (this._connectionState === next && !reconnected) return
    this._connectionState = next
    for (const handler of this._connectionStateHandlers) handler(next, { reconnected })
  }

  _dispatch(event) {
    let frame
    try {
      frame = JSON.parse(event.data)
    } catch (e) {
      return
    }
    if (frame.type === 'pong') {
      this._clearPongTimer()
      return
    }
    const handlers = this._subscribers.get(frame.type)
    if (handlers === undefined) return
    for (const handler of [...handlers]) handler(frame)
  }

  _clearPongTimer() {
    if (this._pongTimer !== null) {
      clearTimeout(this._pongTimer)
      this._pongTimer = null
    }
  }

  _stopHeartbeat() {
    if (this._pingTimer !== null) {
      clearInterval(this._pingTimer)
      this._pingTimer = null
    }
    this._clearPongTimer()
  }

  _startHeartbeat(ws) {
    this._stopHeartbeat()
    this._pingTimer = setInterval(() => {
      if (ws.readyState !== 1) return
      ws.send(JSON.stringify({ type: 'ping' }))
      // A NAT or a load balancer that quietly dropped the connection leaves
      // the socket looking open forever — an unanswered ping is what
      // actually detects that, rather than waiting for TCP to notice.
      if (this._pongTimer === null) {
        this._pongTimer = setTimeout(() => {
          this._pongTimer = null
          ws.close()
        }, PONG_TIMEOUT_MS)
      }
    }, PING_INTERVAL_MS)
  }

  _scheduleReconnect() {
    if (!this._wanted || this._reconnectTimer !== null) return
    const delay = RECONNECT_DELAYS_MS[Math.min(this._reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)]
    this._reconnectAttempt++
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null
      // A failed attempt rejects; its own close handler schedules the next
      // one, so there is nothing to handle here beyond not throwing.
      this._connectSocket().catch(() => {})
    }, delay)
  }

  _reconnectNow() {
    if (!this._wanted || this._socket !== null || this._connectingPromise !== null) return
    if (this._reconnectTimer !== null) {
      clearTimeout(this._reconnectTimer)
      this._reconnectTimer = null
    }
    this._reconnectAttempt = 0
    this._connectSocket().catch(() => {})
  }

  _connectSocket() {
    if (this._connectingPromise) return this._connectingPromise
    if (this._socket) return Promise.resolve(this._socket)

    const hadConnectedBefore = this._everConnected
    this._setConnectionState('connecting')
    this._connectingPromise = new Promise((resolve, reject) => {
      const ws = createChatSocket()
      let opened = false

      ws.onopen = () => {
        opened = true
        this._socket = ws
        this._connectingPromise = null
        this._reconnectAttempt = 0
        this._everConnected = true
        this._startHeartbeat(ws)
        this._setConnectionState('open', { reconnected: hadConnectedBefore })
        resolve(ws)
      }

      ws.onmessage = (event) => this._dispatch(event)

      ws.onerror = () => {
        // A failed handshake reports both error and close; the close branch
        // is the one that schedules the retry, so this only has to not throw.
      }

      ws.onclose = () => {
        this._socket = null
        this._connectingPromise = null
        this._stopHeartbeat()
        this._setConnectionState('closed')
        if (!opened) reject(new Error('Unable to connect to the chat service.'))
        this._scheduleReconnect()
      }
    })

    return this._connectingPromise
  }
}

export const chatChannel = new ChatChannel()
