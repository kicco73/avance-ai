<script setup>
// Settings > Manage projects: one row per project with its three-state
// status (see backend ProjectService._project_status) and revision info.
// The status dot toggles running <-> manually_paused only; 'paused' needs an external fix.
import { onMounted, ref } from 'vue'
import { getProjectsRuntimeStatus, putProjectPause, putProjectResume } from '../../api.js'
import { confirmDialog } from '../../dialogStore.js'
import ErrorBanner from '../ErrorBanner.vue'

// Emits events only; App.vue owns the actual new/upload/delete actions.
const emit = defineEmits(['close', 'new-project', 'upload', 'delete', 'edit', 'benchmark', 'download', 'chat', 'wipe-live-sessions'])

const rows = ref([])
const loading = ref(true)
// Name of the project with a pause/resume request in flight; disables
// only that row's button.
const togglingProject = ref(null)

async function load() {
  loading.value = true
  try {
    const res = await getProjectsRuntimeStatus()
    rows.value = res.projects
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

function replaceRow(updated) {
  const idx = rows.value.findIndex((r) => r.name === updated.name)
  if (idx !== -1) rows.value[idx] = updated
}

async function toggleStatus(row) {
  if (row.status === 'paused') return // the automatic case — nothing to toggle here
  if (row.status === 'running') {
    const ok = await confirmDialog({
      title: 'Pause project',
      body: `Pause "${row.name}"? Live chat on this project will show a maintenance screen until it's resumed.`,
      okLabel: 'Pause'
    })
    if (!ok) return
  }
  togglingProject.value = row.name
  try {
    const updated = row.status === 'running'
      ? await putProjectPause(row.name)
      : await putProjectResume(row.name)
    replaceRow(updated)
  } catch {
    // already surfaced via apiFetch
  } finally {
    togglingProject.value = null
  }
}

// App.vue's delete handler has no confirm of its own, so this is the
// one place that asks before deleting.
async function selectDelete(name) {
  const ok = await confirmDialog({
    title: 'Delete project',
    body: `Delete project "${name}"? This cannot be undone.`,
    okLabel: 'Delete',
    danger: true
  })
  if (!ok) return
  emit('delete', name)
}

function selectEdit(name) {
  emit('edit', name)
}

function selectBenchmark(name) {
  emit('benchmark', name)
}

function selectChat(name) {
  emit('chat', name)
}

function selectDownload(name) {
  emit('download', name)
}

// This view's own confirm, same pattern as selectDelete above — the
// caller (App.vue) just performs the wipe, no confirm of its own.
async function selectWipeLiveSessions(name) {
  const ok = await confirmDialog({
    title: 'Wipe live sessions',
    body: `Delete every user's live conversation for "${name}"? This cannot be undone.`,
    okLabel: 'Wipe',
    danger: true
  })
  if (!ok) return
  emit('wipe-live-sessions', name)
}

function statusLabel(status) {
  if (status === 'running') return 'Running'
  if (status === 'manually_paused') return 'Manually paused'
  return 'Paused'
}

function statusTitle(row) {
  if (row.status === 'running') return 'Click to manually pause'
  if (row.status === 'manually_paused') return 'Click to resume'
  return row.paused_reason ?? 'Paused'
}

onMounted(load)

defineExpose({ refresh: load })
</script>

<template>
  <div class="manage-projects-overlay">
    <div class="manage-projects-header">
      <h2>Manage projects</h2>
      <div class="manage-projects-header-actions">
        <button class="manage-projects-action-btn" @click="emit('new-project')">New project</button>
        <button class="manage-projects-action-btn" @click="emit('upload')">Upload project...</button>
        <button class="close-btn" @click="emit('close')">Back</button>
      </div>
    </div>

    <ErrorBanner />

    <div class="manage-projects-body">
      <p v-if="loading" class="manage-projects-status">Loading…</p>
      <p v-else-if="!rows.length" class="manage-projects-status">No projects yet.</p>

      <table v-else class="manage-projects-table">
        <thead>
          <tr>
            <th class="manage-projects-col-status"></th>
            <th>Project</th>
            <th>Status</th>
            <th>Reason</th>
            <th>Revision</th>
            <th>Published</th>
            <th class="manage-projects-col-chat"></th>
            <th class="manage-projects-col-benchmark"></th>
            <th class="manage-projects-col-download"></th>
            <th class="manage-projects-col-wipe"></th>
            <th class="manage-projects-col-delete"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.name">
            <td class="manage-projects-col-status">
              <button
                type="button"
                class="manage-projects-icon-btn"
                :class="`manage-projects-icon-${row.status}`"
                :disabled="row.status === 'paused' || togglingProject === row.name"
                :title="statusTitle(row)"
                @click="toggleStatus(row)"
              >
                <span class="manage-projects-dot"></span>
              </button>
            </td>
            <td class="manage-projects-name">
              <button type="button" class="manage-projects-name-btn" title="Edit project" @click="selectEdit(row.name)">{{ row.name }}</button>
            </td>
            <td>{{ statusLabel(row.status) }}</td>
            <td class="manage-projects-reason">{{ row.paused_reason ?? '—' }}</td>
            <td>{{ row.revision }}</td>
            <td>{{ row.published_revision ?? '—' }}</td>
            <td class="manage-projects-col-chat">
              <button
                type="button"
                class="manage-projects-chat-btn"
                title="Open chat"
                @click="selectChat(row.name)"
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor">
                  <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z" />
                </svg>
              </button>
            </td>
            <td class="manage-projects-col-benchmark">
              <button
                type="button"
                class="manage-projects-benchmark-btn"
                title="Label sessions"
                @click="selectBenchmark(row.name)"
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor">
                  <path d="M21.41 11.58l-9-9C12.05 2.22 11.55 2 11 2H4c-1.1 0-2 .9-2 2v7c0 .55.22 1.05.59 1.41l9 9c.36.36.86.59 1.41.59s1.05-.23 1.41-.59l7-7c.37-.36.59-.86.59-1.41s-.23-1.06-.59-1.42zM6.5 8C5.67 8 5 7.33 5 6.5S5.67 5 6.5 5 8 5.67 8 6.5 7.33 8 6.5 8z" />
                </svg>
              </button>
            </td>
            <td class="manage-projects-col-download">
              <button
                type="button"
                class="manage-projects-download-btn"
                title="Download project"
                @click="selectDownload(row.name)"
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor">
                  <path d="M12 3a1 1 0 0 1 1 1v9.59l2.3-2.3a1 1 0 1 1 1.4 1.42l-4 4a1 1 0 0 1-1.4 0l-4-4a1 1 0 1 1 1.4-1.42l2.3 2.3V4a1 1 0 0 1 1-1zM5 19a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H6a1 1 0 0 1-1-1z" />
                </svg>
              </button>
            </td>
            <td class="manage-projects-col-wipe">
              <button
                type="button"
                class="manage-projects-wipe-btn"
                title="Wipe live sessions"
                @click="selectWipeLiveSessions(row.name)"
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor">
                  <path d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" />
                </svg>
              </button>
            </td>
            <td class="manage-projects-col-delete">
              <button
                type="button"
                class="manage-projects-delete-btn"
                title="Delete project"
                @click="selectDelete(row.name)"
              >×</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.manage-projects-overlay {
  position: fixed;
  inset: 0;
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.manage-projects-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.manage-projects-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.manage-projects-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.close-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.close-btn:hover {
  background: #4a6fa5;
  color: white;
}

.manage-projects-action-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.manage-projects-action-btn:hover {
  background: #4a6fa5;
  color: white;
}

.manage-projects-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem;
}

.manage-projects-status {
  margin: 0;
  padding: 0.75rem 0;
  font-size: 0.9rem;
  color: #666;
}

.manage-projects-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}

.manage-projects-table th {
  text-align: left;
  padding: 0.5rem 0.75rem;
  border-bottom: 2px solid #ddd;
  color: #555;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.manage-projects-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #eee;
  vertical-align: middle;
}

.manage-projects-col-status {
  width: 2.2rem;
}

.manage-projects-col-chat {
  width: 2.2rem;
}

.manage-projects-col-benchmark {
  width: 2.2rem;
}

.manage-projects-col-download {
  width: 2.2rem;
}

.manage-projects-col-wipe {
  width: 2.2rem;
}

.manage-projects-col-delete {
  width: 2.2rem;
}

.manage-projects-name {
  font-weight: 600;
  color: #333;
}

.manage-projects-name-btn {
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  font-weight: 600;
  color: #4a6fa5;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.manage-projects-name-btn:hover {
  color: #2e4c78;
}

.manage-projects-reason {
  color: #666;
  max-width: 320px;
}

.manage-projects-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: none;
  cursor: pointer;
}

.manage-projects-icon-btn:not(:disabled):hover {
  background: #f0f4fa;
}

.manage-projects-icon-btn:disabled {
  cursor: not-allowed;
}

.manage-projects-dot {
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 50%;
}

.manage-projects-icon-running .manage-projects-dot {
  background: #2e7d32;
}

.manage-projects-icon-paused .manage-projects-dot {
  background: #b06a00;
}

.manage-projects-icon-manually_paused .manage-projects-dot {
  background: #607d8b;
}

.manage-projects-chat-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: none;
  color: #4a6fa5;
  cursor: pointer;
}

.manage-projects-chat-btn:hover {
  background: #f0f4fa;
}

.manage-projects-benchmark-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: none;
  color: #4a6fa5;
  cursor: pointer;
}

.manage-projects-benchmark-btn:hover {
  background: #f0f4fa;
}

.manage-projects-download-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: none;
  color: #4a6fa5;
  cursor: pointer;
}

.manage-projects-download-btn:hover {
  background: #f0f4fa;
}

.manage-projects-wipe-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: none;
  color: #c62828;
  cursor: pointer;
}

.manage-projects-wipe-btn:hover {
  background: #fdecea;
}

.manage-projects-delete-btn {
  width: 1.6rem;
  height: 1.6rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #c62828;
  cursor: pointer;
  font-size: 1rem;
}

.manage-projects-delete-btn:hover {
  background: #fdecea;
}
</style>
