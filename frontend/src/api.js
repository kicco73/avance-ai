const API_URL = '/api'
const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`

async function handleResponse(res) {
  if (!res.ok) {
    let detail = `Error ${res.status}`
    try {
      const body = await res.json()
      if (body.detail) detail = body.detail
    } catch {
      // ignore non-JSON body
    }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  return res.json()
}

export function getState() {
  return fetch(`${API_URL}/state`).then(handleResponse)
}

export function getMessages() {
  return fetch(`${API_URL}/messages`).then(handleResponse)
}

// Chat runs over a websocket: the backend pushes status updates (retrying,
// done, failed) as they happen instead of the client polling for them.
export function createChatSocket() {
  return new WebSocket(WS_URL)
}

export function getSignals() {
  return fetch(`${API_URL}/signals`).then(handleResponse)
}

export function postAction(actionName) {
  return fetch(`${API_URL}/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_name: actionName })
  }).then(handleResponse)
}

export function getAutoTracking() {
  return fetch(`${API_URL}/autotracking`).then(handleResponse)
}

export function postAutoTracking(enabled) {
  return fetch(`${API_URL}/autotracking`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  }).then(handleResponse)
}

export function postTriggersPreview(signals) {
  return fetch(`${API_URL}/triggers/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ signals })
  }).then(handleResponse)
}

export function postReset() {
  return fetch(`${API_URL}/reset`, { method: 'POST' }).then(handleResponse)
}
