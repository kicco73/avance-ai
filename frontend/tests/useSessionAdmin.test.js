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
    it('tracks importingSessions/importProgress and clears them when done', async () => {
      let progressCb
      postImportSessions.mockImplementation((_project, _files, onProgress) => {
        progressCb = onProgress
        return Promise.resolve({ last_session_id: null, results: [] })
      })
      summarizeImportFailures.mockReturnValue(null)
      const s = mount()

      const p = s.handleImportSession(['file1'])
      expect(s.importingSessions.value).toBe(true)
      progressCb?.({ percentage: 50 })
      expect(s.importProgress.value).toBe(50)
      await p

      expect(s.importingSessions.value).toBe(false)
      expect(s.importProgress.value).toBeNull()
    })

    it('on a new session id, refreshes the list then selects the newly-imported session', async () => {
      postImportSessions.mockResolvedValue({ last_session_id: 99, results: [] })
      summarizeImportFailures.mockReturnValue(null)
      const importedSession = { id: 99 }
      refreshSessionsQuietly.mockImplementation(() => {
        sessions.value = [importedSession]
        return Promise.resolve()
      })
      const selectSession = vi.fn()
      const s = mount({ selectSession })

      await s.handleImportSession(['file1'])

      expect(refreshSessionsQuietly).toHaveBeenCalledWith(true, 'proj')
      expect(selectSession).toHaveBeenCalledWith(importedSession)
    })

    it('surfaces a failure summary via setApiError, or clears it when there is none', async () => {
      postImportSessions.mockResolvedValue({ last_session_id: null, results: ['x'] })
      summarizeImportFailures.mockReturnValue({ message: 'oops', detail: 'd' })
      const s = mount()

      await s.handleImportSession(['file1'])

      expect(setApiError).toHaveBeenCalledWith('oops', 'd')
      expect(clearApiError).not.toHaveBeenCalled()
    })

    it('a request failure leaves importingSessions/importProgress cleared without touching the session list', async () => {
      postImportSessions.mockRejectedValue(new Error('network'))
      const s = mount()

      await s.handleImportSession(['file1'])

      expect(s.importingSessions.value).toBe(false)
      expect(s.importProgress.value).toBeNull()
      expect(refreshSessionsQuietly).not.toHaveBeenCalled()
    })
  })

  it('onMoveSessions reassigns then refreshes the session list', async () => {
    const s = mount()
    await s.onMoveSessions({ sessionIds: [1, 2], username: 'bob' })

    expect(putSessionsReassign).toHaveBeenCalledWith('proj', [1, 2], 'bob')
    expect(refreshSessionsQuietly).toHaveBeenCalledWith(true, 'proj')
  })

  describe('onDeleteTestUser', () => {
    it('does nothing without confirmation', async () => {
      confirmDialog.mockResolvedValue(false)
      const s = mount()
      await s.onDeleteTestUser({ testUserSeq: 3 })
      expect(deleteTestUser).not.toHaveBeenCalled()
    })

    it('deletes, clears the active session only if it belonged to that test user, and refreshes', async () => {
      confirmDialog.mockResolvedValue(true)
      const s = mount({ sessionId: 5, session: { username: 'Test user 3' } })

      await s.onDeleteTestUser({ testUserSeq: 3 })

      expect(deleteTestUser).toHaveBeenCalledWith('proj', 3)
      expect(s.currentSessionId.value).toBeNull()
      expect(refreshSessionsQuietly).toHaveBeenCalledWith(true, 'proj')
    })

    it('leaves an unrelated active session alone', async () => {
      confirmDialog.mockResolvedValue(true)
      const s = mount({ sessionId: 5, session: { username: 'someone else' } })

      await s.onDeleteTestUser({ testUserSeq: 3 })

      expect(s.currentSessionId.value).toBe(5)
    })
  })

  describe('onDeleteUserSessions', () => {
    it('deletes and clears the active session only if it belonged to that username', async () => {
      confirmDialog.mockResolvedValue(true)
      const s = mount({ sessionId: 5, session: { username: 'alice' } })

      await s.onDeleteUserSessions({ username: 'alice' })

      expect(deleteUserSessions).toHaveBeenCalledWith('proj', 'alice')
      expect(s.currentSessionId.value).toBeNull()
    })
  })

  describe('handleDeleteAllImported', () => {
    it('does nothing without confirmation', async () => {
      confirmDialog.mockResolvedValue(false)
      const s = mount()
      await s.handleDeleteAllImported()
      expect(deleteImportedSessions).not.toHaveBeenCalled()
    })

    it('clears the active session only if it was imported, then refreshes, leaving deletingAllImported false once settled', async () => {
      confirmDialog.mockResolvedValue(true)
      const s = mount({ sessionId: 5, isImported: true })

      await s.handleDeleteAllImported()

      expect(deleteImportedSessions).toHaveBeenCalledWith('proj')
      expect(s.currentSessionId.value).toBeNull()
      expect(s.deletingAllImported.value).toBe(false)
    })

    it('leaves a live (non-imported) active session alone', async () => {
      confirmDialog.mockResolvedValue(true)
      const s = mount({ sessionId: 5, isImported: false })

      await s.handleDeleteAllImported()

      expect(s.currentSessionId.value).toBe(5)
    })
  })

  describe('handleDeleteSession', () => {
    it('does nothing without confirmation', async () => {
      confirmDialog.mockResolvedValue(false)
      const s = mount()
      await s.handleDeleteSession({ id: 5, title: 'x' })
      expect(deleteSession).not.toHaveBeenCalled()
    })

    it('clears the active session only if it is the one deleted, then refreshes, leaving deletingSessionId null once settled', async () => {
      confirmDialog.mockResolvedValue(true)
      const s = mount({ sessionId: 5 })

      await s.handleDeleteSession({ id: 5, title: 'x' })

      expect(deleteSession).toHaveBeenCalledWith(5)
      expect(s.currentSessionId.value).toBeNull()
      expect(s.deletingSessionId.value).toBeNull()
    })

    it('leaves the active session alone when a different session is deleted', async () => {
      confirmDialog.mockResolvedValue(true)
      const s = mount({ sessionId: 5 })

      await s.handleDeleteSession({ id: 9, title: 'x' })

      expect(s.currentSessionId.value).toBe(5)
    })
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
