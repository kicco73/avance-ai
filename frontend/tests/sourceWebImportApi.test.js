// The Source card's "AI Web Import" button talks to the backend the same
// way the project upload does: one POST whose own response streams the
// job's SSE progress chunks. Only fetch is stubbed, so a module-level
// mistake in projectEditor.js throws here instead of being swallowed.
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

let postSourceWebImport

beforeAll(async () => {
  vi.stubEnv('VITE_API_URL', 'http://localhost:8000/api')
  vi.resetModules()
  ;({ postSourceWebImport } = await import('../src/api/projectEditor.js'))
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

describe('postSourceWebImport', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('POSTs the query to the source\'s web-import route, forwards every step\'s percentage, and resolves with the result', async () => {
    fetch.mockResolvedValue(fakeSseResponse([
      { key: 'web-import', queue_status: 'running', percentage: 0 },
      { key: 'web-import', queue_status: 'running', percentage: 25 },
      { key: 'web-import', queue_status: 'running', percentage: 50 },
      { key: 'web-import', queue_status: 'running', percentage: 75 },
      {
        key: 'web-import', queue_status: 'exited', job_status: 'completed', percentage: 100,
        result: { success: true, source: 'places', rows: 2 }
      }
    ]))
    const onProgress = vi.fn()

    const result = await postSourceWebImport('hello world', 'places', 'dentists in Barcelona', onProgress)

    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, options] = fetch.mock.calls[0]
    expect(url).toBe('http://localhost:8000/api/projects/hello%20world/sources/places/web-import')
    expect(options.method).toBe('POST')
    expect(JSON.parse(options.body)).toEqual({ query: 'dentists in Barcelona' })
    expect(onProgress.mock.calls.map(([message]) => message.percentage)).toEqual([0, 25, 50, 75, 100])
    expect(result).toEqual({ success: true, source: 'places', rows: 2 })
  })

  it('rejects when the job reports job_status: failed', async () => {
    fetch.mockResolvedValue(fakeSseResponse([
      { queue_status: 'exited', job_status: 'failed', error: 'The web search returned no result to read.' }
    ]))

    await expect(postSourceWebImport('hello_world', 'places', 'nothing'))
      .rejects.toThrow('The web search returned no result to read.')
  })
})
