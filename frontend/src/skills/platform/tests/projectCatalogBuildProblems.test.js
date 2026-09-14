import { describe, it, expect, vi } from 'vitest'

vi.mock('../api.js', () => ({ getProjectGraph: vi.fn() }))
vi.mock('../../../identifierRegistry.js', () => ({ refreshIdentifierRegistry: vi.fn() }))
vi.mock('../projectFiles.js', () => ({ refreshProjectFiles: vi.fn() }))

import { getProjectGraph } from '../api.js'
import { useProjectCatalog } from '../useProjectCatalog.js'

function broken(fields) {
  const err = new Error('3 problems in index.yml:')
  err.code = 'project_broken'
  err.fields = fields
  return err
}

describe('useProjectCatalog', () => {
  it('keeps every problem the build reported, with its own line', async () => {
    const problems = [
      { message: "State 'a': 'whichever' is not a field a state has", line: 6, section: 'states.a' },
      { message: "Action 'go': 'bogus' is not a field an action has", line: 10, section: 'states.a.actions.go' },
    ]
    vi.mocked(getProjectGraph).mockImplementation(() => Promise.reject(broken({ problems, line: 6 })))
    const catalog = useProjectCatalog('p')

    await catalog.refreshCatalog()

    expect(catalog.projectBroken.value).toBe(true)
    expect(catalog.buildProblems.value).toEqual(problems)
  })

  it('falls back to the message itself when a refusal carries no list', async () => {
    vi.mocked(getProjectGraph).mockImplementation(() => Promise.reject(broken(undefined)))
    const catalog = useProjectCatalog('p')

    await catalog.refreshCatalog()

    expect(catalog.buildProblems.value).toEqual([
      { message: '3 problems in index.yml:', line: null, section: null },
    ])
  })

  it('drops them once the project builds again', async () => {
    const catalog = useProjectCatalog('p')
    vi.mocked(getProjectGraph).mockImplementation(() => Promise.reject(broken({ problems: [{ message: 'x', line: 1, section: 'states.a' }] })))
    await catalog.refreshCatalog()

    vi.mocked(getProjectGraph).mockImplementation(() => Promise.resolve({ nodes: [], edges: [], build_warnings: [] }))
    await catalog.refreshCatalog()

    expect(catalog.buildProblems.value).toEqual([])
    expect(catalog.projectBroken.value).toBe(false)
  })
})
