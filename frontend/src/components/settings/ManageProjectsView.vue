<script setup>
// Settings > Manage projects: one row per project with its three-state
// status (see backend ProjectService._project_status) and revision info.
// The status dot toggles running <-> manually_paused only; 'paused' needs an external fix.
import { onMounted, ref } from 'vue'
import { getProjectMetadata, getProjectsRuntimeStatus, projectFileContentUrl, putProjectPause, putProjectResume } from '../../api.js'
import { confirmDialog } from '../../dialogStore.js'
import ProgressSpinner from '../ProgressSpinner.vue'
import ModelMenu from '../ModelMenu.vue'
import SettingsMenu from './SettingsMenu.vue'
import ProfileMenu from '../ProfileMenu.vue'
import avanceLogoUrl from '../../assets/avance-logo.png'

const props = defineProps({
  uploading: { type: Boolean, default: false },
  // 0-100, or null before the first progress chunk has arrived — see
  // SessionsTree.vue's identical importProgress for the same reasoning.
  uploadProgress: { type: Number, default: null },
  // This is an admin's own landing page now (see App.vue's role-based
  // routing) — no Back button to fall back on, so its own Settings menu
  // (same one the main chat screen shows) is how it reaches every other
  // top-level view instead.
  role: { type: String, default: null },
  // ProfileMenu.vue's own avatar/name — App.vue already fetched this once
  // during boot (see its own `profile` prop docstring), passed straight
  // through so this landing page can show the same topbar avatar the main
  // chat screen does.
  profile: { type: Object, default: null }
})

// Emits events only; App.vue owns the actual new/upload/delete actions.
// The Settings-menu ones (manage-projects/manage-users/label-sessions/
// about/download-backup/restore-backup) are a plain pass-through of
// SettingsMenu.vue's own emits; profile/logout are the same pass-through
// of ProfileMenu.vue's own.
const emit = defineEmits([
  'new-project', 'upload', 'delete', 'edit', 'label', 'download', 'chat', 'wipe-live-sessions',
  'manage-projects', 'manage-users', 'label-sessions', 'edit-projects', 'about', 'download-backup', 'restore-backup',
  'profile', 'logout'
])

const rows = ref([])
const loading = ref(true)
// Name of the project with a pause/resume request in flight; disables
// only that row's button.
const togglingProject = ref(null)
// name -> { id, ui_label, ui_description }, filled in after the runtime-status
// list loads — the project detail card's own content (title/id/description).
const metadataByName = ref({})
// name -> true once that project's icon.png request has failed (missing file),
// same fallback idiom as ProfileMenu.vue's avatar image.
const iconFailedByName = ref({})

async function loadMetadata(names) {
  const results = await Promise.allSettled(names.map((name) => getProjectMetadata(name)))
  results.forEach((result, i) => {
    if (result.status === 'fulfilled') metadataByName.value[names[i]] = result.value.project
  })
}

async function load() {
  loading.value = true
  try {
    const res = await getProjectsRuntimeStatus()
    rows.value = res.projects
    loadMetadata(rows.value.map((row) => row.name))
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

function projectTitle(name) {
  return metadataByName.value[name]?.ui_label || name
}

function projectDescription(name) {
  return metadataByName.value[name]?.ui_description || null
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

function selectLabelSessions(name) {
  emit('label', name)
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
        <button
          class="manage-projects-action-btn"
          :class="{ 'manage-projects-action-btn-busy': uploading }"
          :disabled="uploading"
          @click="emit('upload')"
        >
          <ProgressSpinner v-if="uploading" :progress="uploadProgress" />
          {{ uploading ? (uploadProgress != null ? `Uploading… ${Math.round(uploadProgress)}%` : 'Uploading…') : 'Upload project...' }}
        </button>
        <ModelMenu />
        <SettingsMenu
          :role="role"
          align="right"
          @manage-projects="emit('manage-projects')"
          @manage-users="emit('manage-users')"
          @label-sessions="emit('label-sessions')"
          @edit-projects="emit('edit-projects')"
          @about="emit('about')"
          @download-backup="emit('download-backup')"
          @restore-backup="(file) => emit('restore-backup', file)"
        />
        <ProfileMenu :profile="profile" @profile="emit('profile')" @logout="emit('logout')" />
      </div>
    </div>

    <div class="manage-projects-body">
      <p v-if="loading" class="manage-projects-status">Loading…</p>
      <p v-else-if="!rows.length" class="manage-projects-status">No projects yet.</p>

      <table v-else class="manage-projects-table">
        <tbody>
          <tr v-for="row in rows" :key="row.name">
            <td class="manage-projects-name">
              <button type="button" class="project-card" title="Edit project" @click="selectEdit(row.name)">
                <img
                  v-if="!iconFailedByName[row.name]"
                  :src="projectFileContentUrl(row.name, 'aspect/icon.png')"
                  class="project-card-icon"
                  alt=""
                  @error="iconFailedByName[row.name] = true"
                />
                <img v-else :src="avanceLogoUrl" class="project-card-fallback-logo" alt="" />
                <span class="project-card-body">
                  <span class="project-card-title-row">
                    <span class="project-card-title">{{ projectTitle(row.name) }}</span>
                    <span v-if="row.published_revision != null" class="project-card-rev">rev. {{ row.published_revision }}</span>
                  </span>
                  <span v-if="projectDescription(row.name)" class="project-card-desc">{{ projectDescription(row.name) }}</span>
                </span>
              </button>
            </td>
            <td class="manage-projects-col-status">
              <span class="manage-projects-status-cell">
                <button
                  type="button"
                  class="manage-projects-icon-btn"
                  :class="`manage-projects-icon-${row.status}`"
                  :disabled="row.status === 'paused' || togglingProject === row.name"
                  :title="statusTitle(row)"
                  @click="toggleStatus(row)"
                >
                  <svg v-if="row.status === 'manually_paused'" class="manage-projects-play-icon" viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                  <span v-else class="manage-projects-dot"></span>
                </button>
                {{ statusLabel(row.status) }}
              </span>
            </td>
            <td class="manage-projects-col-chat">
              <button
                type="button"
                class="manage-projects-chat-btn"
                title="Open chat"
                @click="selectChat(row.name)"
              >
                <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                  <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z" />
                </svg>
              </button>
            </td>
            <td class="manage-projects-col-label">
              <button
                type="button"
                class="manage-projects-label-btn"
                title="Label sessions"
                @click="selectLabelSessions(row.name)"
              >
                <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
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
                <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
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
                <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
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

.manage-projects-action-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
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

.manage-projects-action-btn:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.manage-projects-action-btn-busy:hover {
  background: white;
  color: #4a6fa5;
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
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 0.88rem;
}

.manage-projects-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #eee;
  vertical-align: middle;
}

.manage-projects-col-status {
  width: 11rem;
  white-space: nowrap;
  text-align: left;
}

.manage-projects-status-cell {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}

.manage-projects-col-chat {
  width: 3.24rem;
}

.manage-projects-col-label {
  width: 3.24rem;
}

.manage-projects-col-download {
  width: 3.24rem;
}

.manage-projects-col-wipe {
  width: 3.24rem;
}

.manage-projects-col-delete {
  width: 3.24rem;
}

.project-card {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  width: 100%;
  max-width: 320px;
  padding: 0.5rem 0.7rem;
  border: 1px solid #eee;
  border-radius: 8px;
  background: #fafafa;
  cursor: pointer;
  text-align: left;
  font: inherit;
}

.project-card:hover {
  border-color: #4a6fa5;
  background: #f3f6fb;
}

.project-card-icon {
  flex-shrink: 0;
  width: 65px;
  height: 65px;
  border-radius: 15px;
  object-fit: cover;
}

.project-card-fallback-logo {
  flex-shrink: 0;
  width: 65px;
  height: 65px;
  object-fit: contain;
  opacity: 0.25;
}

.project-card-body {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}

.project-card-title-row {
  display: flex;
  align-items: baseline;
  gap: 0.35rem;
  min-width: 0;
}

.project-card-title {
  min-width: 0;
  font-size: 0.85rem;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.project-card-rev {
  flex-shrink: 0;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  background: #eee;
  color: #888;
  font-size: 0.65rem;
  font-weight: 500;
}

.project-card-desc {
  font-size: 0.75rem;
  color: #777;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
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
  animation: manage-projects-dot-pulse 2.2s ease-in-out infinite;
}

@keyframes manage-projects-dot-pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

.manage-projects-icon-paused .manage-projects-dot {
  background: #b06a00;
}

.manage-projects-icon-manually_paused .manage-projects-dot {
  background: #607d8b;
}

.manage-projects-play-icon {
  color: #607d8b;
}

.manage-projects-chat-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2.88rem;
  height: 2.88rem;
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

.manage-projects-label-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2.88rem;
  height: 2.88rem;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: none;
  color: #4a6fa5;
  cursor: pointer;
}

.manage-projects-label-btn:hover {
  background: #f0f4fa;
}

.manage-projects-download-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2.88rem;
  height: 2.88rem;
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
  width: 2.88rem;
  height: 2.88rem;
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
  width: 2.88rem;
  height: 2.88rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #c62828;
  cursor: pointer;
  font-size: 1.4rem;
}

.manage-projects-delete-btn:hover {
  background: #fdecea;
}
</style>
