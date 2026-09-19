<script setup>
import { confirmDialog } from '../../../dialogStore.js'
import ProgressSpinner from '../../../components/ProgressSpinner.vue'
import { useServerOperations } from '../useServerOperations.js'

const {
  backupDownload, handleWipeAllLiveSessions, handleCleanUnusedRevisions, handleClearTranslations,
  handleDownloadBackup, handleRestoreBackup,
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

async function selectCleanUnusedRevisions() {
  const ok = await confirmDialog({
    title: 'Clean unused revisions',
    body: 'Delete every project revision that is not published and not used by any session? This cannot be undone.',
    okLabel: 'Clean'
  })
  if (!ok) return
  await handleCleanUnusedRevisions()
}

async function selectClearTranslations() {
  const ok = await confirmDialog({
    title: 'Clear translation cache',
    body: 'Delete every cached label translation? A translated button will simply be asked for again the next time it is needed.',
    okLabel: 'Clear'
  })
  if (!ok) return
  await handleClearTranslations()
}
</script>

<template>
  <div class="services-section">
    <div class="services-actions-row">
      <button
        type="button"
        class="services-action-btn services-download-btn"
        :disabled="!!backupDownload"
        :title="backupDownload && backupDownload.percentage != null ? `Downloading… ${Math.round(backupDownload.percentage)}%` : null"
        @click="handleDownloadBackup()"
      >
        <span
          v-if="backupDownload"
          class="services-download-fill"
          :style="{ width: `${backupDownload.percentage ?? 0}%` }"
        ></span>
        <span class="services-download-label">
          <ProgressSpinner v-if="backupDownload" :progress="backupDownload.percentage" />
          Download backup
        </span>
      </button>
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
      <button type="button" class="services-action-btn" @click="selectClearTranslations">Clear translation cache</button>
    </div>
  </div>
</template>

<style scoped>
.services-section { margin-top: 0.75rem; }
.services-actions-row { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.services-action-btn { padding: 0.45rem 0.9rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; font-size: 0.85rem; }
.services-action-btn:hover { background: #f2f5f9; }
.services-download-btn { position: relative; overflow: hidden; }
.services-download-btn:disabled { cursor: default; color: #444; }
.services-download-btn:disabled:hover { background: white; }
.services-download-fill { position: absolute; inset: 0 auto 0 0; background: #dbe7f6; transition: width 0.15s linear; }
.services-download-label { position: relative; display: inline-flex; align-items: center; gap: 0.4rem; }
.services-action-btn-danger { border-color: #c0392b; color: #c0392b; }
.services-action-btn-danger:hover { background: #fdecea; }
.services-restore-label { display: inline-flex; align-items: center; }
.services-restore-input { display: none; }
</style>
