<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getProjects } from '../api.js'

const emit = defineEmits([
  'select',
  'edit',
  'benchmark',
  'new-project',
  'upload',
  'download',
  'delete',
  'download-backup',
  'restore-backup'
])

const open = ref(false)
const loading = ref(false)
const projects = ref([])
const activeProjectName = ref(null)
const rootEl = ref(null)
const restoreInput = ref(null)

const noProjectsAvailable = computed(() => projects.value.length === 0)
const editDisabled = computed(() => noProjectsAvailable.value)
const benchmarkDisabled = computed(() => noProjectsAvailable.value)
const deleteDisabled = computed(() => noProjectsAvailable.value)

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

function selectBenchmark() {
  if (benchmarkDisabled.value || !activeProjectName.value) return
  open.value = false
  emit('benchmark', activeProjectName.value)
}

function selectNewProject() {
  open.value = false
  emit('new-project')
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
  event.target.value = ''
  if (!file) return
  emit('restore-backup', file)
}

function selectDelete() {
  if (deleteDisabled.value || !activeProjectName.value) return

  const name = activeProjectName.value
  if (!window.confirm(`Delete project "${name}"? This cannot be undone.`)) return

  open.value = false
  emit('delete', name)
}

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
    <button
      class="projects-btn"
      :title="activeProjectName ?? 'Projects'"
      @click="toggle"
    >
      {{ activeProjectName ?? 'Projects' }}
    </button>

    <div v-if="open" class="projects-panel">
      <p v-if="loading" class="projects-status">Loading…</p>

      <ul v-else class="projects-list">
        <!-- Fixed actions first -->
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
          <button
            class="projects-item projects-edit-item"
            :disabled="benchmarkDisabled"
            @click="selectBenchmark"
          >
            Label sessions
          </button>
        </li>

        <li>
          <button
            class="projects-item projects-upload-item"
            @click="selectNewProject"
          >
            New project
          </button>
        </li>

        <li>
          <button
            class="projects-item projects-upload-item"
            @click="selectUpload"
          >
            Upload project...
          </button>
        </li>

        <li>
          <button
            class="projects-item projects-backup-item"
            @click="selectDownloadBackup"
          >
            Download backup
          </button>
        </li>

        <li>
          <button
            class="projects-item projects-restore-item"
            @click="selectRestoreBackup"
          >
            Restore backup...
          </button>
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

        <!-- Dynamic projects at the bottom -->
        <li
          v-for="name in projects"
          :key="name"
          class="project-entry"
        >
          <button
            class="projects-item"
            @click="selectProject(name)"
          >
            <span class="projects-item-check">
              {{ name === activeProjectName ? '✓' : '' }}
            </span>
            {{ name }}
          </button>
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

/* Fixed options */
.projects-edit-item {
  color: #4a6fa5;
}

.projects-edit-item:disabled {
  color: #ccc;
  cursor: not-allowed;
}

.projects-upload-item {
  color: #4a6fa5;
}

.projects-backup-item {
  border-top: 1px solid #eee;
  color: #4a6fa5;
}

.projects-restore-item {
  color: #4a6fa5;
}

.projects-delete-item {
  color: #c62828;
}

.projects-delete-item:disabled {
  color: #ccc;
  cursor: not-allowed;
}

/* Separator before the dynamic project list */
.project-entry:first-of-type {
  border-top: 1px solid #ccc;
  margin-top: 0.25rem;
}

.project-entry + .project-entry {
  border-top: 1px solid #eee;
}

.restore-backup-input {
  display: none;
}
</style>