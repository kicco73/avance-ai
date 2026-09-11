import { apiFetch } from '../../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

// Operating the deployment: the whole database in and out, wiping every
// live conversation, clearing revisions nothing points at. Nothing about
// the domain changes because nobody asked for a backup — which is why
// these are a panel's, not the core's.

export function getBackup() {
  return apiFetch(`${API_URL}/skills/platform/settings/backup`, {}, { parse: 'blob' })
}

export function postRestoreBackup(file) {
  return apiFetch(`${API_URL}/skills/platform/settings/backup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file
  })
}

// Settings > Manage services > Database — wipes live sessions across
// every project at once, not just one.
export function postWipeAllLiveSessions() {
  return apiFetch(`${API_URL}/skills/platform/settings/database/wipe-live-sessions`, { method: 'POST' })
}

// Settings > Manage services > Database — deletes every archive revision,
// across every project, that's neither published, the current draft, nor
// pinned by any session. Returns {success, deleted} — deleted is how many
// distinct revisions were actually removed.
export function postCleanUnusedRevisions() {
  return apiFetch(`${API_URL}/skills/platform/settings/database/clean-unused-revisions`, { method: 'POST' })
}
