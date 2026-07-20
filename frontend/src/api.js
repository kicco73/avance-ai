const BASE_URL = 'http://localhost:8000'

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
  return fetch(`${BASE_URL}/api/state`).then(handleResponse)
}

export function postChat(message) {
  return fetch(`${BASE_URL}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message })
  }).then(handleResponse)
}

export function postAction(actionName) {
  return fetch(`${BASE_URL}/api/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_name: actionName })
  }).then(handleResponse)
}

export function postReset() {
  return fetch(`${BASE_URL}/api/reset`, { method: 'POST' }).then(handleResponse)
}
