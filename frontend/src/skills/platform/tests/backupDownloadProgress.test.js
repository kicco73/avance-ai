import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

let getBackup

beforeAll(async () => {
  vi.stubEnv('VITE_API_URL', 'http://localhost:8000/api')
  vi.resetModules()
  ;({ getBackup } = await import('../api/serverOps.js'))
})

function fakeFileResponse(chunks, { announcesLength = true } = {}) {
  const queue = [...chunks]
  const headers = new Map([['Content-Type', 'application/octet-stream']])
  if (announcesLength) headers.set('Content-Length', String(chunks.reduce((n, c) => n + c.length, 0)))
  return {
    ok: true,
    status: 200,
    headers: { get: (name) => headers.get(name) ?? null },
    body: {
      getReader: () => ({
        read: async () => (queue.length ? { done: false, value: queue.shift() } : { done: true, value: undefined })
      })
    }
  }
}

describe('getBackup', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reports how much of the file has arrived and resolves with all of its bytes', async () => {
    fetch.mockResolvedValue(fakeFileResponse([new Uint8Array([1, 2]), new Uint8Array([3, 4]), new Uint8Array([5, 6, 7, 8])]))
    const onProgress = vi.fn()

    const blob = await getBackup(onProgress)

    expect(onProgress.mock.calls.map(([message]) => message.percentage)).toEqual([25, 50, 100])
    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]))
  })

  it('reports an unknown percentage when the response has no Content-Length', async () => {
    fetch.mockResolvedValue(fakeFileResponse([new Uint8Array([1, 2])], { announcesLength: false }))
    const onProgress = vi.fn()

    await getBackup(onProgress)

    expect(onProgress).toHaveBeenCalledWith({ percentage: null })
  })
})
