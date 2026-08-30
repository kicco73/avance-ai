import { setApiError } from '../errorStore.js'
import { requireLogin } from '../authStore.js'
import { emitProjectChanged } from '../projectChangeEvents.js'

// Reads a `text/event-stream` body of `data: {...}\n\n` chunks, calling
// `onProgress` for each one, until a `completed`/`failed` chunk arrives —
// used by postImportSessions to show real progress instead of a spinner.
async function readSseResult(res, onProgress) {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let final = null
  while (!final) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let boundary
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const chunk = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      if (!chunk.startsWith('data: ')) continue
      const message = JSON.parse(chunk.slice('data: '.length))
      onProgress?.(message)
      if (message.queue_status === 'exited') final = message
    }
  }
  if (final?.job_status === 'failed') {
    const message = final.error || 'Import failed.'
    setApiError(message, '')
    throw new Error(message)
  }
  return final?.result ?? null
}

export async function apiFetch(url, options, { parse = 'json', onProgress, onCommitted } = {}) {
  let res
  try {
    // The session cookie is httpOnly and, in dev, often cross-origin
    // (VITE_API_URL pointing at a separate backend port) — without this
    // it simply never gets sent, and every call 401s regardless of login.
    res = await fetch(url, { ...options, credentials: 'include' })
  } catch (err) {
    if (err.name === 'AbortError') throw err
    setApiError('Unable to reach the backend.', err.message)
    throw err
  }

  if (!res.ok) {
    let message = `Error ${res.status}`
    let detail = ''
    try {
      const body = await res.json()
      if (body?.error?.message) {
        message = body.error.message
        detail = body.error.detail ?? ''
      }
    } catch {

    }
    // A 401 means "not logged in" — LoginView.vue takes over the whole
    // screen for that, so it doesn't also need an error banner.
    if (res.status === 401) {
      requireLogin()
    } else {
      setApiError(message, detail)
    }
    const err = new Error(message)
    err.status = res.status
    err.detail = detail
    throw err
  }

  onCommitted?.()

  // A 204 has no body — res.json() on an empty response throws, regardless
  // of the requested `parse` mode.
  if (res.status === 204) return null
  if (parse === 'blob') return res.blob()
  if (parse === 'text') return res.text()
  if (parse === 'sse') return readSseResult(res, onProgress)
  if (parse === 'response') return res
  return res.json()
}

export async function projectFetch(projectName, url, options, fetchOpts) {
  const result = await apiFetch(url, options, fetchOpts)
  await emitProjectChanged(projectName)
  return result
}
