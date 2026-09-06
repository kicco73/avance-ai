import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api.js', () => ({
  getProjectSources: vi.fn(),
  postAddSource: vi.fn(),
  putSourceField: vi.fn(),
  deleteProjectSource: vi.fn(),
}))

import { getProjectSources, postAddSource, putSourceField, deleteProjectSource } from '../src/api.js'
import { useProjectSources } from '../src/composables/useProjectSources.js'

const PINO = { name: 'pino', ui_label: 'Flights', ui_description: null, url: 'avance:behaviour/flights.csv' }
const CITIES = { name: 'cities', ui_label: 'Cities', ui_description: null, url: 'avance:behaviour/cities.csv' }

function sourceList(...sources) {
  return { sources: sources.map((source) => ({ source })) }
}

describe('useProjectSources', () => {
  let flashRecentlyAdded, guardedAction, s

  beforeEach(() => {
    vi.clearAllMocks()
    flashRecentlyAdded = vi.fn()
    // Simulates a never-dirty editor: guardedAction just runs immediately,
    // matching useIndexYmlEditing.test.js's own identical stand-in.
    guardedAction = vi.fn((label, run) => run())
    getProjectSources.mockResolvedValue(sourceList(PINO))
    s = useProjectSources('proj', guardedAction, flashRecentlyAdded)
  })

  it('loadSources exposes the declared sources, which selectSource then resolves by name (null for none or an unknown one)', async () => {
    await s.loadSources()

    expect(getProjectSources).toHaveBeenCalledWith('proj')
    expect(s.sources.value).toHaveLength(1)
    expect(s.sourcesLoading.value).toBe(false)
    expect(s.selectedSource.value).toBeNull()

    s.selectSource('pino')
    expect(s.currentSourceName.value).toBe('pino')
    expect(s.selectedSource.value).toEqual(PINO)

    s.selectSource('does-not-exist')
    expect(s.selectedSource.value).toBeNull()
  })

  it('handleAddSource creates through the given driver, reloads, selects and flashes the new source', async () => {
    postAddSource.mockResolvedValue({ name: 'behaviour' })
    getProjectSources.mockResolvedValueOnce(sourceList()).mockResolvedValueOnce(
      sourceList({ name: 'behaviour', ui_label: 'behaviour', ui_description: null, url: '' })
    )

    s.handleAddSource()

    expect(guardedAction).toHaveBeenCalledWith('add a new source', expect.any(Function))
    // handleAddSource is fire-and-forget (matches useIndexYmlEditing.js's
    // own handleAddState) — its own extra `await loadSources()` hop means
    // waiting on a fixed number of microtask ticks would be fragile, so
    // this polls instead.
    await vi.waitFor(() => expect(s.currentSourceName.value).toBe('behaviour'))
    expect(postAddSource).toHaveBeenCalledWith('proj', 'avance')
    expect(flashRecentlyAdded).toHaveBeenCalledWith('source:behaviour')

    postAddSource.mockResolvedValue({ name: 'env' })
    getProjectSources.mockResolvedValueOnce(sourceList()).mockResolvedValueOnce(
      sourceList({ name: 'env', ui_label: 'env', ui_description: null, url: 'avance:env' })
    )

    s.handleAddSource('env')

    await vi.waitFor(() => expect(s.currentSourceName.value).toBe('env'))
    expect(postAddSource).toHaveBeenCalledWith('proj', 'env')
  })

  it('handleSetSourceField needs a selected source, then edits it and follows a rename', async () => {
    await s.handleSetSourceField('ui-label', 'New label')
    expect(putSourceField).not.toHaveBeenCalled()

    await s.loadSources()
    s.selectSource('pino')
    putSourceField.mockResolvedValue({ name: 'flight_records' })
    getProjectSources.mockResolvedValueOnce(sourceList({ ...PINO, name: 'flight_records' }))

    s.handleSetSourceField('name', 'Flight Records')

    await vi.waitFor(() => expect(s.currentSourceName.value).toBe('flight_records'))
    expect(putSourceField).toHaveBeenCalledWith('proj', 'pino', 'name', 'Flight Records')
  })

  it('handleDeleteSource clears the selection only when the deleted source was the selected one', async () => {
    getProjectSources.mockResolvedValue(sourceList(PINO, CITIES))
    await s.loadSources()
    s.selectSource('pino')

    s.handleDeleteSource('cities')

    await vi.waitFor(() => expect(deleteProjectSource).toHaveBeenCalledWith('proj', 'cities'))
    expect(s.currentSourceName.value).toBe('pino')

    getProjectSources.mockResolvedValueOnce(sourceList())
    s.handleDeleteSource('pino')

    await vi.waitFor(() => expect(s.currentSourceName.value).toBeNull())
    expect(deleteProjectSource).toHaveBeenCalledWith('proj', 'pino')
    expect(s.deletingSource.value).toBeNull()
  })
})
