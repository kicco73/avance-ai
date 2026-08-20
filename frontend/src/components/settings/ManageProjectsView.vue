<script setup>
// Settings > Manage projects (formerly "Runtime status", widened to
// actually manage projects from here too — New/Upload/Delete, moved out
// of ProjectsMenu.vue, which now stays scoped to switch/edit/label/
// download only) — one row per project: its own three-state availability
// (see backend's ProjectService._project_status: 'running'/'paused'/
// 'manually_paused') and enough context (revision, published revision,
// paused reason) to actually understand why. The status icon next to a
// project's own name is also its own toggle: running -> pause,
// manually_paused -> resume, both enforced backend-side (see
// ProjectService.set_manually_paused/set_manually_running) — 'paused'
// (the automatic case) has nothing to toggle from here at all, since the
// real fix is whatever's actually broken (its own build, or a
// dependency), not a button on this table.
import { onMounted, ref } from 'vue'
import { getProjectsRuntimeStatus, putProjectPause, putProjectResume } from '../../api.js'
import ErrorBanner from '../ErrorBanner.vue'

// "New project"/"Upload project..."/"delete" all live here now, not
// ProjectsMenu.vue — App.vue still owns the actual actions
// (postNewProject/the shared hidden file input/deleteProject+
// clearChatUi), this only emits the same events those buttons used to,
// so App.vue's own handlers/wiring didn't need to change at all, just
// which component they're attached to.
const emit = defineEmits(['close', 'new-project', 'upload', 'delete', 'edit', 'benchmark'])

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

// Same confirm text ProjectsMenu.vue's own selectDelete used before this
// moved here — App.vue's own handleModelDelete does the actual work
// (deleteProject + clearChatUi + refreshStateAndProjects, which also
// refreshes this table's own rows, see App.vue) and has no confirm of
// its own, so this is the one place that still needs to ask.
function selectDelete(name) {
  if (!window.confirm(`Delete project "${name}"? This cannot be undone.`)) return
  emit('delete', name)
}

function selectEdit(name) {
  emit('edit', name)
}

function selectBenchmark(name) {
  emit('benchmark', name)
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
            <th class="manage-projects-col-benchmark"></th>
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

.manage-projects-col-benchmark {
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
