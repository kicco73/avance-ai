<script setup>
// The operations an administrator performs on the deployment, offered
// inside Settings' own Data tab. The tab itself is core — it describes
// what this deployment has configured — and these change it, so they are
// contributed rather than built in (see skills/registry.js's
// servicesTabActions). A build without this panel shows the tab with
// nothing to press.
import { confirmDialog } from '../../../dialogStore.js'
import { useServerOperations } from '../useServerOperations.js'

const {
  handleWipeAllLiveSessions, handleCleanUnusedRevisions, handleDownloadBackup, handleRestoreBackup,
} = useServerOperations()

async function selectWipeAllLiveSessions() {
  const ok = await confirmDialog({
    title: 'Wipe all live sessions',
    body: 'Delete every live conversation across every project? This cannot be undone.',
    okLabel: 'Wipe',
    danger: true
  })
  if (!ok) return
  await handleWipeAllLiveSessions()
}

// Only ever removes archive revisions that are already unreachable
// (superseded drafts, never published, no session pinned to them) — safe
// by construction, unlike the wipe above, so no danger styling.
async function selectCleanUnusedRevisions() {
  const ok = await confirmDialog({
    title: 'Clean unused revisions',
    body: 'Delete every project revision that is not published and not used by any session? This cannot be undone.',
    okLabel: 'Clean'
  })
  if (!ok) return
  await handleCleanUnusedRevisions()
}
</script>

<template>
  <div class="services-section">
    <div class="services-actions-row">
      <button type="button" class="services-action-btn" @click="handleDownloadBackup()">Download backup</button>
      <label class="services-action-btn services-restore-label">
        Restore backup...
        <input
          type="file"
          accept=".sqlite"
          class="services-restore-input"
          @change="(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) handleRestoreBackup(f) }"
        />
      </label>
      <button type="button" class="services-action-btn services-action-btn-danger" @click="selectWipeAllLiveSessions">Wipe all live sessions</button>
      <button type="button" class="services-action-btn" @click="selectCleanUnusedRevisions">Clean unused revisions</button>
    </div>
  </div>
</template>

<style scoped>
.services-section { margin-top: 0.75rem; }
.services-actions-row { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.services-action-btn { padding: 0.45rem 0.9rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 0.85rem; }
.services-action-btn:hover { background: #f2f5f9; }
.services-action-btn-danger { border-color: #c0392b; color: #c0392b; }
.services-action-btn-danger:hover { background: #fdecea; }
.services-restore-label { display: inline-flex; align-items: center; }
.services-restore-input { display: none; }
</style>
