import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { projectFetch, projectFetchAnnouncing } from '../src/api/core.js'
import { onProjectChanged } from '../src/projectChangeEvents.js'

function answered(body) {
  return { ok: true, status: 200, json: async () => body }
}

describe('an edit that announces a project change', () => {
  let finishRefresh
  let heard
  let unsubscribe

  beforeEach(() => {
    global.fetch = vi.fn(async () => answered({ project_id: 'burn_out', new_name: 'media/b.jpg' }))
    heard = []
    unsubscribe = onProjectChanged((projectId) => {
      heard.push(projectId)
      return new Promise((resolve) => { finishRefresh = resolve })
    })
  })

  afterEach(() => {
    finishRefresh?.()
    unsubscribe()
  })

  it('answers its caller before the listeners have finished refreshing', async () => {
    const result = await projectFetchAnnouncing('burn_out', '/api/x', { method: 'POST' })

    expect(result.new_name).toBe('media/b.jpg')
    expect(heard).toEqual(['burn_out'])
  })

  it('is unlike an edit whose caller reads what the refresh produced, which waits for it', async () => {
    let answeredCaller = false
    const pending = projectFetch('burn_out', '/api/x', { method: 'POST' }).then(() => { answeredCaller = true })
    await vi.waitFor(() => expect(heard).toEqual(['burn_out']))

    expect(answeredCaller).toBe(false)
    finishRefresh()
    await pending
    expect(answeredCaller).toBe(true)
  })
})
