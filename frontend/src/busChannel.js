import { createChatSocket } from './api.js'

const PING_INTERVAL_MS = 25000
const PONG_TIMEOUT_MS = 10000
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 16000, 30000]

export const SWITCHED_TO_OTHER_CLIENT = 'switched_to_other_client'
export const SUPERSEDED_CLOSE_CODE = 4410

export const SERVER_EVENTS = [
  'ui.notification',
  'session.taken_over',
  'ui.system_warning',
  'ui.progress',
  'human_prompt'
]

class ChatChannel {
  constructor() {
    this._socket = null
    this._connectingPromise = null
    this._connectionState = 'closed'
    this._wanted = false
    this._reconnectAttempt = 0
    this._reconnectTimer = null
    this._pingTimer = null
    this._pongTimer = null
    this._everConnected = false
    this._subscribers = new Map()
    this._connectionStateHandlers = new Set()
    this._onOnline = () => this._reconnectNow()
    this._onVisibility = () => {
      if (document.visibilityState === 'visible') this._reconnectNow()
    }
  }

  subscribe(type, handler) {
    let handlers = this._subscribers.get(type)
    if (handlers === undefined) {
      handlers = new Set()
      this._subscribers.set(type, handlers)
    }
    const wasEmpty = handlers.size === 0
    handlers.add(handler)
    if (wasEmpty) this._register('subscribe', type)
    return () => {
      handlers.delete(handler)
      if (handlers.size === 0) this._register('unsubscribe', type)
    }
  }

  _registeredEvents() {
    return SERVER_EVENTS.filter((type) => (this._subscribers.get(type)?.size ?? 0) > 0)
  }

  _register(action, type) {
    if (!SERVER_EVENTS.includes(type)) return
    this.send({ type: action, events: [type] })
  }

  _registerAll() {
    const events = this._registeredEvents()
    if (events.length) this.send({ type: 'subscribe', events })
  }

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

  _settleSuperseded() {
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
    this._setConnectionState('superseded')
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
    if (frame.type === SWITCHED_TO_OTHER_CLIENT) {
      this._settleSuperseded()
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
        this._registerAll()
        this._setConnectionState('open', { reconnected: hadConnectedBefore })
        resolve(ws)
      }

      ws.onmessage = (event) => this._dispatch(event)

      ws.onerror = () => {
      }

      ws.onclose = (event) => {
        this._socket = null
        this._connectingPromise = null
        this._stopHeartbeat()
        if (event?.code === SUPERSEDED_CLOSE_CODE || this._connectionState === 'superseded') {
          this._settleSuperseded()
          if (!opened) reject(new Error('Another client took over this chat.'))
          return
        }
        this._setConnectionState('closed')
        if (!opened) reject(new Error('Unable to connect to the chat service.'))
        this._scheduleReconnect()
      }
    })

    return this._connectingPromise
  }
}

export const busChannel = new ChatChannel()
