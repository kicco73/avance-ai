<script setup>
// Settings > Runtime status — one row per project, its own three-state
// availability (see backend's ProjectService._project_status: 'running'/
// 'paused'/'manually_paused') and enough context (revision, published
// revision, paused reason) to actually understand why. The status icon
// to a project's own name is also its own toggle: running -> pause,
// manually_paused -> resume, both enforced backend-side (see
// ProjectService.set_manually_paused/set_manually_running) — 'paused'
// (the automatic case) has nothing to toggle from here at all, since the
// real fix is whatever's actually broken (its own build, or a
// dependency), not a button on this table.
import { onMounted, ref } from 'vue'
import { getProjectsRuntimeStatus, putProjectPause, putProjectResume } from '../../api.js'

const emit = defineEmits(['close'])

const rows = ref([])
const loading = ref(true)
// Which project's own status button has a pause/resume request in
// flight — disables just that one row's button, same convention as
// SessionsPanel.vue's own deletingSessionId.
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
    if (!window.confirm(`Pause "${row.name}"? Live chat on this project will show a maintenance screen until it's resumed.`)) return
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
</script>

<template>
  <div class="runtime-status-overlay">
    <div class="runtime-status-header">
      <h2>Runtime status</h2>
      <div class="runtime-status-header-actions">
        <button class="close-btn" @click="emit('close')">Back</button>
      </div>
    </div>

    <div class="runtime-status-body">
      <p v-if="loading" class="runtime-status-status">Loading…</p>
      <p v-else-if="!rows.length" class="runtime-status-status">No projects yet.</p>

      <table v-else class="runtime-status-table">
        <thead>
          <tr>
            <th class="runtime-status-col-status"></th>
            <th>Project</th>
            <th>Status</th>
            <th>Reason</th>
            <th>Revision</th>
            <th>Published</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.name">
            <td class="runtime-status-col-status">
              <button
                type="button"
                class="runtime-status-icon-btn"
                :class="`runtime-status-icon-${row.status}`"
                :disabled="row.status === 'paused' || togglingProject === row.name"
                :title="statusTitle(row)"
                @click="toggleStatus(row)"
              >
                <span class="runtime-status-dot"></span>
              </button>
            </td>
            <td class="runtime-status-name">{{ row.name }}</td>
            <td>{{ statusLabel(row.status) }}</td>
            <td class="runtime-status-reason">{{ row.paused_reason ?? '—' }}</td>
            <td>{{ row.revision }}</td>
            <td>{{ row.published_revision ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.runtime-status-overlay {
  position: fixed;
  inset: 0;
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.runtime-status-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.runtime-status-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.runtime-status-header-actions {
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

.runtime-status-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem;
}

.runtime-status-status {
  margin: 0;
  padding: 0.75rem 0;
  font-size: 0.9rem;
  color: #666;
}

.runtime-status-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}

.runtime-status-table th {
  text-align: left;
  padding: 0.5rem 0.75rem;
  border-bottom: 2px solid #ddd;
  color: #555;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.runtime-status-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #eee;
  vertical-align: middle;
}

.runtime-status-col-status {
  width: 2.2rem;
}

.runtime-status-name {
  font-weight: 600;
  color: #333;
}

.runtime-status-reason {
  color: #666;
  max-width: 320px;
}

.runtime-status-icon-btn {
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

.runtime-status-icon-btn:not(:disabled):hover {
  background: #f0f4fa;
}

.runtime-status-icon-btn:disabled {
  cursor: not-allowed;
}

.runtime-status-dot {
  width: 0.7rem;
  height: 0.7rem;
  border-radius: 50%;
}

.runtime-status-icon-running .runtime-status-dot {
  background: #2e7d32;
}

.runtime-status-icon-paused .runtime-status-dot {
  background: #b06a00;
}

.runtime-status-icon-manually_paused .runtime-status-dot {
  background: #607d8b;
}
</style>
