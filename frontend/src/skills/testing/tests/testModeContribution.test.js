import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

vi.mock('../../../api.js', () => ({
  getMessages: vi.fn().mockResolvedValue([]),
  getSessionSignals: vi.fn().mockResolvedValue([]),
  getUsers: vi.fn().mockResolvedValue({ users: [{ id: 'u1', email: 'a@b.c' }] }),
  getProjectSignals: vi.fn().mockResolvedValue({ signals: [{ signal: { name: 'mood' } }] }),
}))
vi.mock('../../../chatStore.js', () => ({
  sessions: ref([{ id: 7, username: 'a@b.c', type: 'native', start_state: 'Start', end_state: 'End' }]),
}))

import { selection } from '../selection.js'
import { key, projectModes, servicesTabs } from '../index.js'

const workspace = {
  projectId: 'proj',
  stateElementFor: (stateKey) => ({ id: stateKey }),
}

describe('the testing skill as the platform sees it', () => {
  it('is named after its own directory and contributes one project mode', () => {
    expect(key).toBe('testing')
    expect(projectModes).toHaveLength(1)
    expect(projectModes[0].id).toBe('test')
    expect(projectModes[0].panel).toBeTruthy()
    expect(servicesTabs.map((tab) => tab.id)).toEqual(['testing'])
  })

  it('namespaces every inspector tab it contributes', () => {
    for (const tab of projectModes[0].inspectorTabs) {
      expect(tab.id.startsWith('testing-')).toBe(true)
      expect(tab.component).toBeTruthy()
    }
  })
})

describe('the test mode selection', () => {
  beforeEach(() => {
    selection.open('proj', workspace)
  })

  it('starts with nothing selected and resolves a state through the workspace', () => {
    expect(selection.nodeId.value).toBe(null)

    selection.handleAutoSelect('state:Greeting')

    expect(selection.stateKey.value).toBe('Greeting')
    expect(selection.element.value).toEqual({ id: 'Greeting' })
  })

  it('resolves a selected session and the user behind it', async () => {
    selection.handleAutoSelect('session:7')
    await vi.waitFor(() => expect(selection.user.value).toBeTruthy())

    expect(selection.session.value.id).toBe(7)
    expect(selection.user.value.email).toBe('a@b.c')
  })

  it('resolves a selected signal by name', async () => {
    selection.handleAutoSelect('signal:mood')
    await vi.waitFor(() => expect(selection.signal.value).toBeTruthy())

    expect(selection.signalName.value).toBe('mood')
    expect(selection.signal.value.name).toBe('mood')
  })

  it('forgets the previous selection when it is opened again', () => {
    selection.handleAutoSelect('state:Greeting')
    selection.open('other', workspace)

    expect(selection.nodeId.value).toBe(null)
    expect(selection.stateKey.value).toBe(null)
  })
})
