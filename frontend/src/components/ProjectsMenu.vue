<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getProjects } from '../api.js'

const emit = defineEmits([
  'select',
  'download'
])

const open = ref(false)
const loading = ref(false)
// {name, is_paused, ui_label}[] (see ProjectService.list_projects,
// Prompt 7/9) — the status dot next to each project's own name (see the
// template below) reads is_paused directly off this, never recomputed
// client-side; ui_label (Prompt 9's own `project.ui-label`) is shown in
// place of the raw name wherever declared, same "declared label, falls
// back to the raw name/key" convention already used for a state/signal/
// action's own ui-label.
const projects = ref([])
const activeProjectName = ref(null)
const rootEl = ref(null)

// The active project's own row (for its declared ui_label) — falls back
// to the raw name both before the initial loadProjects() call resolves
// and for a project that never declared a ui_label at all.
const activeProjectLabel = computed(() => {
  return projects.value.find((p) => p.name === activeProjectName.value)?.ui_label ?? activeProjectName.value
})

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
      :title="activeProjectLabel ?? 'Projects'"
      @click="toggle"
    >
      {{ activeProjectLabel ?? 'Projects' }}
    </button>

    <div v-if="open" class="projects-panel">
      <p v-if="loading" class="projects-status">Loading…</p>

      <ul v-else class="projects-list">
        <li
          v-for="project in projects"
          :key="project.name"
          class="project-entry"
        >
          <button
            class="projects-item"
            @click="selectProject(project.name)"
          >
            <span class="projects-item-check">
              {{ project.name === activeProjectName ? '✓' : '' }}
            </span>
            <span
              class="projects-item-status"
              :class="project.is_paused ? 'projects-item-status-paused' : 'projects-item-status-running'"
              :title="project.is_paused ? 'Paused' : 'Running'"
            ></span>
            {{ project.ui_label ?? project.name }}
          </button>
        </li>
      </ul>
    </div>
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

.project-entry + .project-entry {
  border-top: 1px solid #eee;
}

/* Prompt 7 — running/paused status dot, next to each project's own name. */
.projects-item-status {
  display: inline-block;
  flex-shrink: 0;
  width: 0.5rem;
  height: 0.5rem;
  margin-right: 0.4rem;
  border-radius: 50%;
}

.projects-item-status-running {
  background: #2e7d32;
}

.projects-item-status-paused {
  background: #b06a00;
}
</style>