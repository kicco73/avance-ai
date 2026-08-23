import { createChatSocket, postChatMessage } from './api.js'
import { setApiError } from './errorStore.js'

class WebSocketUnavailableError extends Error {}

let websocketUnavailable = false
let socket = null
let socketConnectingPromise = null
let pendingTurn = null // { resolve, reject, onStatus, onChunk }

// A server-pushed cross-project wake-up — never a response to anything
// this client asked for, so it never touches pendingTurn, and can arrive
// for a project other than the one currently displayed.
let notificationHandler = null

export function onNotification(handler) {
  notificationHandler = handler
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

function handleSocketMessage(event) {
  let data
  try {
    data = JSON.parse(event.data)
  } catch (e) {
    if (pendingTurn?.onChunk) {
      pendingTurn.onChunk(event.data)
    }
    return
  }

  // Streaming chunk
  if (data.type === 'chunk' || data.chunk !== undefined || data.delta !== undefined) {
    const textChunk = data.content ?? data.chunk ?? data.delta ?? ''
    if (textChunk && pendingTurn?.onChunk) {
      pendingTurn.onChunk(textChunk)
    }
    return
  }

  // Status / retry
  if (data.type === 'status' || data.status) {
    const statusText = data.status ?? data.message
    if (statusText && pendingTurn?.onStatus) {
      pendingTurn.onStatus(statusText)
    }
    return
  }

  if (data.type === 'retrying') {
    const seconds = Math.max(0, Math.ceil(data.retry_in ?? 0))
    pendingTurn?.onStatus?.(`Service unavailable, retrying (${data.attempt}/${data.max_attempts}) in ${seconds}s...`)
    return
  }

  // Error
  if (data.type === 'error') {
    setApiError(data.error.message, data.error.detail)
    pendingTurn?.reject(new Error(data.error.message))
    pendingTurn = null
    return
  }

  // Done: resolves the promise at the end of the stream
  if (data.type === 'done') {
    if (!pendingTurn) return
    const { resolve } = pendingTurn
    pendingTurn = null
    resolve(normalizeResult(data))
    return
  }

  // Notification: server-pushed cross-project wake-up, never tied to
  // pendingTurn — it's up to the registered handler to decide how to show it.
  if (data.type === 'notification') {
    notificationHandler?.({
      project_name: data.project_name,
      state: data.state,
      'on-enter': data['on-enter']
    })
  }
}

function connectSocket() {
  if (socketConnectingPromise) return socketConnectingPromise

  socketConnectingPromise = new Promise((resolve, reject) => {
    const ws = createChatSocket()
    let opened = false

    ws.onopen = () => {
      opened = true
      socket = ws
      socketConnectingPromise = null
      resolve(ws)
    }

    ws.onmessage = handleSocketMessage

    ws.onerror = () => {}

    ws.onclose = () => {
      socket = null
      socketConnectingPromise = null

      if (!opened) {
        reject(new WebSocketUnavailableError('Unable to connect to the chat service.'))
        return
      }

      if (pendingTurn) {
        const err = new Error('Chat connection closed unexpectedly.')
        setApiError(err.message)
        pendingTurn.reject(err)
        pendingTurn = null
      }
    }
  })

  return socketConnectingPromise
}

async function ensureSocket() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    return socket
  }
  return await connectSocket()
}

async function sendViaWebsocket(text, sessionId, { onStatus, onChunk } = {}) {
  const ws = await ensureSocket()
  return new Promise((resolve, reject) => {
    pendingTurn = { resolve, reject, onStatus, onChunk }
    ws.send(JSON.stringify({ message: text, session_id: sessionId }))
  })
}

async function sendViaRest(text, sessionId) {
  const data = await postChatMessage(text, sessionId)
  return normalizeResult(data)
}

export async function sendMessage(text, sessionId, options = {}) {
  if (!websocketUnavailable) {
    try {
      return await sendViaWebsocket(text, sessionId, options)
    } catch (err) {
      if (!(err instanceof WebSocketUnavailableError)) throw err
      websocketUnavailable = true
    }
  }
  return sendViaRest(text, sessionId)
}

export function disconnect() {
  if (socket) {
    socket.close()
    socket = null
  }
  socketConnectingPromise = null
}