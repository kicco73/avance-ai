import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, ref } from 'vue'

vi.mock('../src/api.js', () => ({
  postImportSessions: vi.fn(),
  getExportSessions: vi.fn(),
  deleteImportedSessions: vi.fn(),
  putSessionsReassign: vi.fn(),
  deleteTestUser: vi.fn(),
  deleteUserSessions: vi.fn(),
  deleteSession: vi.fn(),
}))
vi.mock('../src/chatStore.js', () => ({
  sessions: { value: [] },
  refreshSessionsQuietly: vi.fn(),
}))
vi.mock('../src/sessionImport.js', () => ({
  summarizeImportFailures: vi.fn(),
}))
vi.mock('../src/errorStore.js', () => ({
  setApiError: vi.fn(),
  clearApiError: vi.fn(),
}))
vi.mock('../src/dialogStore.js', () => ({
  confirmDialog: vi.fn(),
}))

import {
  postImportSessions, getExportSessions, deleteImportedSessions, putSessionsReassign,
  deleteTestUser, deleteUserSessions, deleteSession,
} from '../src/api.js'
import { sessions, refreshSessionsQuietly } from '../src/chatStore.js'
import { summarizeImportFailures } from '../src/sessionImport.js'
import { setApiError, clearApiError } from '../src/errorStore.js'
import { confirmDialog } from '../src/dialogStore.js'
import { useSessionAdmin } from '../src/composables/useSessionAdmin.js'

function mountComposable(setup) {
  let result
  const container = document.createElement('div')
  const app = createApp({ setup: () => { result = setup(); return () => null } })
  app.mount(container)
  return { result, unmount: () => app.unmount() }
}

describe('useSessionAdmin', () => {
  let unmount

  beforeEach(() => {
    vi.clearAllMocks()
    sessions.value = []
  })

  afterEach(() => {
    unmount?.()
  })

  function mount({ sessionId = null, session = null, isImported = false, selectSession = vi.fn() } = {}) {
    const currentSessionId = ref(sessionId)
    const currentSession = ref(session)
    const currentSessionIsImported = ref(isImported)
    const mounted = mountComposable(() =>
      useSessionAdmin('proj', currentSessionId, currentSession, currentSessionIsImported, selectSession)
    )
    unmount = mounted.unmount
    return { ...mounted.result, currentSessionId, currentSession, currentSessionIsImported, selectSession }
  }

  describe('handleImportSession', () => {
    it('tracks progress while importing, then refreshes the list and selects the newly-imported session', async () => {
      let progressCb
      postImportSessions.mockImplementation((_project, _files, onProgress) => {
        progressCb = onProgress
        return Promise.resolve({ last_session_id: 99, results: [] })
      })
      summarizeImportFailures.mockReturnValue(null)
      const importedSession = { id: 99 }
      refreshSessionsQuietly.mockImplementation(() => {
        sessions.value = [importedSession]
        return Promise.resolve()
      })
      const selectSession = vi.fn()
      const s = mount({ selectSession })

      const p = s.handleImportSession(['file1'])
      expect(s.importingSessions.value).toBe(true)
      progressCb?.({ percentage: 50 })
      expect(s.importProgress.value).toBe(50)
      await p

      expect(s.importingSessions.value).toBe(false)
      expect(s.importProgress.value).toBeNull()
      expect(refreshSessionsQuietly).toHaveBeenCalledWith(true, 'proj')
      expect(selectSession).toHaveBeenCalledWith(importedSession)
    })

    it('surfaces a failure summary via setApiError, and a request failure leaves the session list untouched', async () => {
      postImportSessions.mockResolvedValue({ last_session_id: null, results: ['x'] })
      summarizeImportFailures.mockReturnValue({ message: 'oops', detail: 'd' })
      const s = mount()

      await s.handleImportSession(['file1'])
      expect(setApiError).toHaveBeenCalledWith('oops', 'd')
      expect(clearApiError).not.toHaveBeenCalled()
      s.unmount?.()

      postImportSessions.mockRejectedValue(new Error('network'))
      refreshSessionsQuietly.mockClear()
      const failing = mount()

      await failing.handleImportSession(['file1'])

      expect(failing.importingSessions.value).toBe(false)
      expect(failing.importProgress.value).toBeNull()
      expect(refreshSessionsQuietly).not.toHaveBeenCalled()
    })
  })

  it('onMoveSessions reassigns then refreshes the session list', async () => {
    const s = mount()
    await s.onMoveSessions({ sessionIds: [1, 2], username: 'bob' })

    expect(putSessionsReassign).toHaveBeenCalledWith('proj', [1, 2], 'bob')
    expect(refreshSessionsQuietly).toHaveBeenCalledWith(true, 'proj')
  })

  it('every destructive action needs confirmation first', async () => {
    confirmDialog.mockResolvedValue(false)
    const s = mount({ sessionId: 5 })

    await s.onDeleteTestUser({ testUserSeq: 3 })
    await s.handleDeleteAllImported()
    await s.handleDeleteSession({ id: 5, title: 'x' })

    expect(deleteTestUser).not.toHaveBeenCalled()
    expect(deleteImportedSessions).not.toHaveBeenCalled()
    expect(deleteSession).not.toHaveBeenCalled()
    expect(s.currentSessionId.value).toBe(5)
  })

  it('deleting a test user or a username clears the active session only when it belonged to them', async () => {
    confirmDialog.mockResolvedValue(true)

    const unrelated = mount({ sessionId: 5, session: { username: 'someone else' } })
    await unrelated.onDeleteTestUser({ testUserSeq: 3 })
    expect(deleteTestUser).toHaveBeenCalledWith('proj', 3)
    expect(unrelated.currentSessionId.value).toBe(5)
    unrelated.unmount?.()

    const theirs = mount({ sessionId: 5, session: { username: 'Test user 3' } })
    await theirs.onDeleteTestUser({ testUserSeq: 3 })
    expect(theirs.currentSessionId.value).toBeNull()
    expect(refreshSessionsQuietly).toHaveBeenCalledWith(true, 'proj')
    theirs.unmount?.()

    const alice = mount({ sessionId: 5, session: { username: 'alice' } })
    await alice.onDeleteUserSessions({ username: 'alice' })
    expect(deleteUserSessions).toHaveBeenCalledWith('proj', 'alice')
    expect(alice.currentSessionId.value).toBeNull()
  })

  it('deleting all imported sessions clears the active one only if it was itself imported', async () => {
    confirmDialog.mockResolvedValue(true)

    const live = mount({ sessionId: 5, isImported: false })
    await live.handleDeleteAllImported()
    expect(deleteImportedSessions).toHaveBeenCalledWith('proj')
    expect(live.currentSessionId.value).toBe(5)
    live.unmount?.()

    const imported = mount({ sessionId: 5, isImported: true })
    await imported.handleDeleteAllImported()
    expect(imported.currentSessionId.value).toBeNull()
    expect(imported.deletingAllImported.value).toBe(false)
  })

  it('deleting one session clears the active one only if it is the very session deleted', async () => {
    confirmDialog.mockResolvedValue(true)

    const other = mount({ sessionId: 5 })
    await other.handleDeleteSession({ id: 9, title: 'x' })
    expect(other.currentSessionId.value).toBe(5)
    other.unmount?.()

    const same = mount({ sessionId: 5 })
    await same.handleDeleteSession({ id: 5, title: 'x' })
    expect(deleteSession).toHaveBeenCalledWith(5)
    expect(same.currentSessionId.value).toBeNull()
    expect(same.deletingSessionId.value).toBeNull()
  })

  it('handleDownloadSessions fetches the export blob and tracks downloadingSessions', async () => {
    const blob = new Blob(['[]'], { type: 'application/json' })
    getExportSessions.mockResolvedValue(blob)
    const createObjectURL = vi.fn(() => 'blob:fake')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const s = mount()

    const p = s.handleDownloadSessions('imported')
    expect(s.downloadingSessions.value).toBe(true)
    await p

    expect(getExportSessions).toHaveBeenCalledWith('proj', 'imported')
    expect(createObjectURL).toHaveBeenCalledWith(blob)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake')
    expect(s.downloadingSessions.value).toBe(false)
    vi.unstubAllGlobals()
  })
})
