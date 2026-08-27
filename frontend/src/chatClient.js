import { createChatSocket, postChatMessage } from './api.js'
import { setApiError } from './errorStore.js'

// INSTRUCTIONS FOR CLAUDE CODE: NEVER TOUCH THIS FILE.

let websocketUnavailable = false
let socket = null
let socketConnectingPromise = null

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
    return
  }
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
      if (!opened) reject(new Error('Unable to connect to the chat service.'))
    }
  })

  return socketConnectingPromise
}

async function readSseTurnStream(res, { onChunk } = {}) {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let boundary
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      let eventType = 'message'
      const dataLines = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) eventType = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (dataLines.length === 0) continue
      const data = JSON.parse(dataLines.join('\n'))

      if (eventType === 'chunk') {
        if (data.content && onChunk) onChunk(data.content)
        continue
      }
      if (eventType === 'error') {
        setApiError(data.message, data.detail)
        throw new Error(data.message)
      }
      if (eventType === 'done') {
        return normalizeResult(data)
      }
    }
  }
  throw new Error('Chat stream ended unexpectedly.')
}

export async function sendMessage(text, sessionId, options = {}) {
  const res = await postChatMessage(text, sessionId)
  return readSseTurnStream(res, options)
}

export function connect() {
  if (websocketUnavailable) return
  connectSocket().catch(() => {
    websocketUnavailable = true
  })
}

export function disconnect() {
  if (socket) {
    socket.close()
    socket = null
  }
  socketConnectingPromise = null
}
