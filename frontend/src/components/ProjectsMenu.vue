<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getProjects } from '../api.js'

const DEFAULT_PROJECT_NAME = 'default'

const emit = defineEmits(['select', 'edit', 'upload', 'download', 'delete', 'download-backup', 'restore-backup'])

const open = ref(false)
const loading = ref(false)
const projects = ref([])
const activeProjectName = ref(null)
const rootEl = ref(null)
const restoreInput = ref(null)

// Edit/delete act on the active project — meaningless (and, for delete,
// actually dangerous: the backend still resolves an "active" project name
// even with zero projects uploaded) once there's nothing to select at all.
const noProjectsAvailable = computed(() => projects.value.length === 0)
const editDisabled = computed(() => noProjectsAvailable.value)
const deleteDisabled = computed(() => noProjectsAvailable.value || activeProjectName.value === DEFAULT_PROJECT_NAME)

// The single fetch behind both the menu's tick and the button's own label —
// called on mount (so the button already shows the right name before the
// menu is ever opened), whenever the dropdown opens, and imperatively via
// `refresh()` (exposed below) after the parent completes a switch/upload/
// delete — never a second, separate call just to relabel the button.
async function loadProjects() {
  loading.value = true
  try {
    const res = await getProjects()
    projects.value = res.projects
    activeProjectName.value = res.active
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

async function toggle() {
  if (open.value) {
    open.value = false
    return
  }
  open.value = true
  await loadProjects()
}

onMounted(loadProjects)

defineExpose({ refresh: loadProjects })

function selectProject(name) {
  open.value = false
  emit('select', name)
}
function selectEdit() {
  if (editDisabled.value || !activeProjectName.value) return
  open.value = false
  emit('edit', activeProjectName.value)
}

function selectUpload() {
  open.value = false
  emit('upload')
}

function selectDownloadBackup() {
  open.value = false
  emit('download-backup')
}

function selectRestoreBackup() {
  open.value = false
  restoreInput.value?.click()
}

function handleRestoreFileChange(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // allow re-selecting the same file afterward
  if (!file) return
  emit('restore-backup', file)
}

// Destructive and irreversible, so confirm via the browser's own dialog
// before emitting — no custom confirm UI to keep in sync.
function selectDelete() {
  if (deleteDisabled.value || !activeProjectName.value) return
  const name = activeProjectName.value
  if (!window.confirm(`Delete project "${name}"? This cannot be undone.`)) return
  open.value = false
  emit('delete', name)
}

// Closing on outside click, matching a standard dropdown affordance — the
// panel otherwise has no other way to dismiss itself.
function handleClickOutside(event) {
  if (open.value && rootEl.value && !rootEl.value.contains(event.target)) {
    open.value = false
  }
}
document.addEventListener('click', handleClickOutside, true)
onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside, true)
})
</script>

<template>
  <div class="projects-menu" ref="rootEl">
    <button class="projects-btn" :title="activeProjectName ?? 'Projects'" @click="toggle">
      {{ activeProjectName ?? 'Projects' }}
    </button>

    <div v-if="open" class="projects-panel">
      <p v-if="loading" class="projects-status">Loading…</p>

      <ul v-else class="projects-list">
        <li v-for="name in projects" :key="name">
          <button class="projects-item" @click="selectProject(name)">
            <span class="projects-item-check">{{ name === activeProjectName ? '✓' : '' }}</span>
            {{ name }}
          </button>
        </li>
        <li>
          <button
            class="projects-item projects-edit-item"
            :disabled="editDisabled"
            @click="selectEdit"
          >
            Edit project
          </button>
        </li>
        <li>
          <button class="projects-item projects-upload-item" @click="selectUpload">Upload project...</button>
        </li>
        <li>
          <button
            class="projects-item projects-delete-item"
            :disabled="deleteDisabled"
            @click="selectDelete"
          >
            Delete project
          </button>
        </li>
        <li>
          <button class="projects-item projects-backup-item" @click="selectDownloadBackup">
            Download backup
          </button>
        </li>
        <li>
          <button class="projects-item" @click="selectRestoreBackup">Restore backup...</button>
        </li>
      </ul>
    </div>

    <input
      ref="restoreInput"
      type="file"
      accept=".sqlite"
      class="restore-backup-input"
      @change="handleRestoreFileChange"
    />
  </div>
</template>

<style scoped>
.projects-menu {
  position: relative;
}

.projects-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  max-width: 160px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.projects-btn:hover {
  background: #4a6fa5;
  color: white;
}

.projects-panel {
  position: absolute;
  top: calc(100% + 0.4rem);
  right: 0;
  min-width: 180px;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 100;
  overflow: hidden;
}

.projects-status {
  margin: 0;
  padding: 0.6rem 0.9rem;
  font-size: 0.85rem;
  color: #444;
}

.projects-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
}

.projects-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.9rem;
  color: #333;
}

.projects-item:hover:not(:disabled) {
  background: #f0f4fa;
}

.projects-item-check {
  display: inline-block;
  width: 1.1rem;
  color: #2e7d32;
  font-weight: 600;
}

.projects-edit-item {
  border-top: 1px solid #eee;
  color: #4a6fa5;
}

.projects-edit-item:disabled {
  color: #ccc;
  cursor: not-allowed;
}

.projects-upload-item {
  color: #4a6fa5;
}

.projects-download-item {
  color: #4a6fa5;
}

.projects-delete-item {
  color: #c62828;
}

.projects-delete-item:disabled {
  color: #ccc;
  cursor: not-allowed;
}

.projects-backup-item {
  border-top: 1px solid #eee;
  color: #4a6fa5;
}

.restore-backup-input {
  display: none;
}
</style>
