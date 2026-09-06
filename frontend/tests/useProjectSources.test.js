import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api.js', () => ({
  getProjectSources: vi.fn(),
  postAddSource: vi.fn(),
  putSourceField: vi.fn(),
  deleteProjectSource: vi.fn(),
}))

import { getProjectSources, postAddSource, putSourceField, deleteProjectSource } from '../src/api.js'
import { useProjectSources } from '../src/composables/useProjectSources.js'

describe('useProjectSources', () => {
  let flashRecentlyAdded, guardedAction, s

  beforeEach(() => {
    vi.clearAllMocks()
    flashRecentlyAdded = vi.fn()
    // Simulates a never-dirty editor: guardedAction just runs immediately,
    // matching useIndexYmlEditing.test.js's own identical stand-in.
    guardedAction = vi.fn((label, run) => run())
    getProjectSources.mockResolvedValue({
      sources: [
        { source: { name: 'pino', ui_label: 'Flights', ui_description: null, url: 'avance:behaviour/flights.csv' } },
      ],
    })
    s = useProjectSources('proj', guardedAction, flashRecentlyAdded)
  })

  it('loadSources fetches and exposes the declared sources', async () => {
    await s.loadSources()

    expect(getProjectSources).toHaveBeenCalledWith('proj')
    expect(s.sources.value).toHaveLength(1)
    expect(s.sourcesLoading.value).toBe(false)
  })

  it('selectSource sets currentSourceName, and selectedSource resolves it off the loaded list', async () => {
    await s.loadSources()

    s.selectSource('pino')

    expect(s.currentSourceName.value).toBe('pino')
    expect(s.selectedSource.value).toEqual({ name: 'pino', ui_label: 'Flights', ui_description: null, url: 'avance:behaviour/flights.csv' })
  })

  it('selectedSource is null while nothing (or an unknown name) is selected', async () => {
    await s.loadSources()
    expect(s.selectedSource.value).toBeNull()

    s.selectSource('does-not-exist')
    expect(s.selectedSource.value).toBeNull()
  })

  it('handleAddSource creates a source, reloads, selects it, and flashes it', async () => {
    postAddSource.mockResolvedValue({ name: 'behaviour' })
    getProjectSources.mockResolvedValueOnce({ sources: [] }).mockResolvedValueOnce({
      sources: [{ source: { name: 'behaviour', ui_label: 'behaviour', ui_description: null, url: '' } }],
    })

    s.handleAddSource()

    expect(guardedAction).toHaveBeenCalledWith('add a new source', expect.any(Function))
    // handleAddSource is fire-and-forget (matches useIndexYmlEditing.js's
    // own handleAddState) — its own extra `await loadSources()` hop means
    // waiting on a fixed number of microtask ticks would be fragile, so
    // this polls instead.
    await vi.waitFor(() => expect(s.currentSourceName.value).toBe('behaviour'))
    expect(postAddSource).toHaveBeenCalledWith('proj', 'avance')
    expect(flashRecentlyAdded).toHaveBeenCalledWith('source:behaviour')
  })

  it('handleAddSource passes an explicit driver through, e.g. for an avance:env source', async () => {
    postAddSource.mockResolvedValue({ name: 'env' })
    getProjectSources.mockResolvedValueOnce({ sources: [] }).mockResolvedValueOnce({
      sources: [{ source: { name: 'env', ui_label: 'env', ui_description: null, url: 'avance:env' } }],
    })

    s.handleAddSource('env')

    await vi.waitFor(() => expect(s.currentSourceName.value).toBe('env'))
    expect(postAddSource).toHaveBeenCalledWith('proj', 'env')
  })

  it('handleSetSourceField does nothing without a selected source', async () => {
    await s.handleSetSourceField('ui-label', 'New label')
    expect(putSourceField).not.toHaveBeenCalled()
  })

  it('handleSetSourceField edits the selected source and follows a rename', async () => {
    await s.loadSources()
    s.selectSource('pino')
    putSourceField.mockResolvedValue({ name: 'flight_records' })
    getProjectSources.mockResolvedValueOnce({
      sources: [{ source: { name: 'flight_records', ui_label: 'Flights', ui_description: null, url: 'avance:behaviour/flights.csv' } }],
    })

    s.handleSetSourceField('name', 'Flight Records')

    await vi.waitFor(() => expect(s.currentSourceName.value).toBe('flight_records'))
    expect(putSourceField).toHaveBeenCalledWith('proj', 'pino', 'name', 'Flight Records')
  })

  it('handleDeleteSource deletes, reloads, and clears the selection if it was the deleted source', async () => {
    await s.loadSources()
    s.selectSource('pino')
    getProjectSources.mockResolvedValueOnce({ sources: [] })

    s.handleDeleteSource('pino')

    await vi.waitFor(() => expect(s.currentSourceName.value).toBeNull())
    expect(deleteProjectSource).toHaveBeenCalledWith('proj', 'pino')
    expect(s.deletingSource.value).toBeNull()
  })

  it('handleDeleteSource leaves an unrelated selection alone', async () => {
    getProjectSources.mockResolvedValue({
      sources: [
        { source: { name: 'pino', ui_label: 'Flights', ui_description: null, url: 'avance:behaviour/flights.csv' } },
        { source: { name: 'cities', ui_label: 'Cities', ui_description: null, url: 'avance:behaviour/cities.csv' } },
      ],
    })
    await s.loadSources()
    s.selectSource('pino')

    s.handleDeleteSource('cities')

    await vi.waitFor(() => expect(deleteProjectSource).toHaveBeenCalledWith('proj', 'cities'))
    expect(s.currentSourceName.value).toBe('pino')
  })
})
