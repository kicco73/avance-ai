import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../api.js', () => ({
  getProjectSources: vi.fn(),
  postAddSource: vi.fn(),
  postAddWebSearchSource: vi.fn(),
  putSourceField: vi.fn(),
  deleteProjectSource: vi.fn(),
}))

import { getProjectSources, postAddSource, postAddWebSearchSource, putSourceField, deleteProjectSource } from '../../../api.js'
import { useProjectSources } from '../useProjectSources.js'

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

  it('handleAddSource creates, reloads, selects and flashes the new source', async () => {
    postAddSource.mockResolvedValue({ name: 'behaviour' })
    getProjectSources.mockResolvedValueOnce(
      sourceList({ name: 'behaviour', ui_label: 'behaviour', ui_description: null, url: 'avance:sources/behaviour.csv' })
    )

    s.handleAddSource()

    expect(guardedAction).toHaveBeenCalledWith('add a new source', expect.any(Function))
    await vi.waitFor(() => expect(s.currentSourceName.value).toBe('behaviour'))
    expect(postAddSource).toHaveBeenCalledWith('proj')
    expect(flashRecentlyAdded).toHaveBeenCalledWith('source:behaviour')
  })

  it('handleAddWebSearchSource creates the websearch-driven source, reloads, selects and flashes it', async () => {
    postAddWebSearchSource.mockResolvedValue({ name: 'websearch' })
    getProjectSources.mockResolvedValueOnce(
      sourceList({ name: 'websearch', ui_label: 'websearch', ui_description: null, url: 'websearch:user' })
    )

    s.handleAddWebSearchSource()

    expect(guardedAction).toHaveBeenCalledWith('add a new web search source', expect.any(Function))
    await vi.waitFor(() => expect(s.currentSourceName.value).toBe('websearch'))
    expect(postAddWebSearchSource).toHaveBeenCalledWith('proj')
    expect(s.selectedSource.value.url).toBe('websearch:user')
    expect(flashRecentlyAdded).toHaveBeenCalledWith('source:websearch')
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
