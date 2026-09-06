// Regression for putProject silently doing nothing: it called projectFetch
// without importing it from core.js, so every upload threw a bare
// ReferenceError inside the try/catch's "already surfaced via apiFetch"
// branch — no request ever left the browser, no console output either.
// Unlike the other API tests, this imports admin.js for real (only fetch
// itself is stubbed) so a module-level mistake like a missing import
// actually throws here instead of being hidden behind a mocked api.js.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

// Pinned here, not read off the running environment: admin.js resolves
// its API_URL from VITE_API_URL at import time, so the stub must land
// before the dynamic import below.
let putProject

beforeAll(async () => {
  vi.stubEnv('VITE_API_URL', 'http://localhost:8000/api')
  vi.resetModules()
  ;({ putProject } = await import('../src/api/admin.js'))
})

function fakeSseResponse(messages) {
  const bytes = new TextEncoder().encode(messages.map((m) => `data: ${JSON.stringify(m)}\n\n`).join(''))
  let sent = false
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (sent) return { done: true, value: undefined }
          sent = true
          return { done: false, value: bytes }
        }
      })
    }
  }
}

describe('putProject', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs the zip to /api/projects/upload, forwards progress, and resolves with the final result', async () => {
    fetch.mockResolvedValue(fakeSseResponse([
      { key: 'import:hello', queue_status: 'running', percentage: 50 },
      { key: 'import:hello', queue_status: 'exited', job_status: 'completed', result: { success: true, project_id: 'hello_world' } }
    ]))
    const file = new File(['zip-bytes'], 'hello.zip')
    const onProgress = vi.fn()

    const result = await putProject(file, onProgress)

    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, options] = fetch.mock.calls[0]
    expect(url).toBe('http://localhost:8000/api/projects/upload')
    expect(options.method).toBe('POST')
    expect(options.headers['Content-Type']).toBe('application/zip')
    expect(options.body).toBe(file)
    expect(onProgress).toHaveBeenCalledWith(expect.objectContaining({ percentage: 50 }))
    expect(result).toEqual({ success: true, project_id: 'hello_world' })
  })

  it('sends application/x-yaml for a bare .yml upload', async () => {
    fetch.mockResolvedValue(fakeSseResponse([
      { queue_status: 'exited', job_status: 'completed', result: {} }
    ]))

    await putProject(new File(['a: b'], 'bare.yml'))

    expect(fetch.mock.calls[0][1].headers['Content-Type']).toBe('application/x-yaml')
  })

  it('rejects when the job reports job_status: failed', async () => {
    fetch.mockResolvedValue(fakeSseResponse([
      { queue_status: 'exited', job_status: 'failed', error: 'Invalid project archive.' }
    ]))

    await expect(putProject(new File(['x'], 'broken.zip'))).rejects.toThrow('Invalid project archive.')
  })
})
