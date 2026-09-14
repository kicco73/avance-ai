import { currentScreen, setApiError } from '../errorStore.js'
import { requireLogin } from '../authStore.js'
import { emitProjectChanged } from '../projectChangeEvents.js'

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
    const message = final.error || 'The job failed.'
    setApiError(message, '')
    throw new Error(message)
  }
  return final?.result ?? null
}

async function readBlobWithProgress(res, onProgress) {
  const total = Number(res.headers.get('Content-Length'))
  const reader = res.body.getReader()
  const chunks = []
  let received = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    chunks.push(value)
    received += value.length
    onProgress?.({ percentage: total ? Math.min(100, (received / total) * 100) : null })
  }
  return new Blob(chunks, { type: res.headers.get('Content-Type') ?? '' })
}

export async function apiFetch(url, options, { parse = 'json', onProgress, onCommitted } = {}) {
  const askedFrom = currentScreen.value
  let res
  try {
    res = await fetch(url, { ...options, credentials: 'include' })
  } catch (err) {
    if (err.name === 'AbortError') throw err
    setApiError('Unable to reach the backend.', err.message, askedFrom)
    throw err
  }

  if (!res.ok) {
    let message = `Error ${res.status}`
    let detail = ''
    let code = null
    let fields = null
    try {
      const body = await res.json()
      if (body?.error?.message) {
        message = body.error.message
        detail = body.error.detail ?? ''
        code = body.error.code ?? null
        fields = body.error.fields ?? null
      }
    } catch {

    }
    if (res.status === 401) {
      requireLogin()
    } else {
      setApiError(message, detail, askedFrom)
    }
    const err = new Error(message)
    err.status = res.status
    err.detail = detail
    err.code = code
    err.fields = fields
    throw err
  }

  onCommitted?.()

  if (res.status === 204) return null
  if (parse === 'blob') return readBlobWithProgress(res, onProgress)
  if (parse === 'text') return res.text()
  if (parse === 'sse') return readSseResult(res, onProgress)
  if (parse === 'response') return res
  return res.json()
}

export async function projectFetch(projectId, url, options, fetchOpts) {
  const result = await apiFetch(url, options, fetchOpts)
  await emitProjectChanged(result?.project_id ?? projectId)
  return result
}
