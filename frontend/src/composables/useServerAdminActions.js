import {
  getState, getAbout, getBackup, postRestoreBackup, postWipeAllLiveSessions, postCleanUnusedRevisions,
} from '../api.js'
import { aboutDialog, confirmDialog, infoDialog } from '../dialogStore.js'
import { handleStateChange, clearChatUi } from '../chatStore.js'
import { emitProjectsChanged } from '../projectChangeEvents.js'

// Running the server, as opposed to authoring what it runs: the whole
// database in and out, wiping live conversations, clearing revisions
// nothing points at, and what version is running. Core, on
// /api/core/settings/… — an operator needs these whether or not an editor
// was ever installed, which is also why they are not in
// useProjectAdminActions.js any more.
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export function useServerAdminActions() {
  async function handleWipeAllLiveSessions() {
    try {
      await postWipeAllLiveSessions()
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleCleanUnusedRevisions() {
    let deleted
    try {
      ({ deleted } = await postCleanUnusedRevisions())
    } catch {
      return
    }
    await infoDialog({
      title: 'Clean unused revisions',
      body: deleted > 0 ? `Deleted ${deleted} unused revision${deleted === 1 ? '' : 's'}.` : 'No unused revisions found.'
    })
  }

  async function handleDownloadBackup() {
    try {
      downloadBlob(await getBackup(), 'avance-backup.sqlite')
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleRestoreBackup(file) {
    const ok = await confirmDialog({
      title: 'Restore backup',
      body: 'Restore this backup? This replaces the entire working database (all projects, sessions, and messages) and cannot be undone.',
      okLabel: 'Restore',
      danger: true
    })
    if (!ok) return
    clearChatUi()
    try {
      await postRestoreBackup(file)
      handleStateChange(await getState())
      await emitProjectsChanged()
    } catch {
      // already surfaced via apiFetch
    }
  }

  async function handleShowAbout() {
    try {
      const about = await getAbout()
      await aboutDialog({ version: about.version })
    } catch {
      // already surfaced via apiFetch
    }
  }

  return {
    handleWipeAllLiveSessions, handleCleanUnusedRevisions,
    handleDownloadBackup, handleRestoreBackup, handleShowAbout,
  }
}
