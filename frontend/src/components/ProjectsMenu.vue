<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getProjects } from '../api.js'

// `selectedName`, when given, overrides which project the button label and
// the ✓ mark reflect — for a caller (e.g. ManageUsersView.vue's own
// project picker) that reuses this menu to choose a project without that
// choice being the app's actual active project. Defaults to `null`, which
// falls back to the normal behavior (the app's own active project).
const props = defineProps({
  selectedName: { type: String, default: null },
  // 'right' (default) anchors the dropdown's own right edge to the
  // button's, opening leftward — for a button that's the last one on the
  // right of its header. 'left' anchors the left edge instead, opening
  // rightward, for a button placed near the left of its header (see
  // LabelProjectView.vue).
  align: { type: String, default: 'right' }
})

const emit = defineEmits([
  'select',
  'download'
])

const open = ref(false)
const loading = ref(false)
// {name, is_paused, ui_label}[] — ui_label is shown in place of the raw
// name wherever declared.
const projects = ref([])
const activeProjectName = ref(null)
const rootEl = ref(null)

const displayedProjectName = computed(() => props.selectedName ?? activeProjectName.value)

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
  if (projects.value.length === 0) return
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
      :class="{ 'projects-btn-disabled': projects.length === 0 }"
      :disabled="projects.length === 0"
      title="Current project"
      @click="toggle"
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M4 4h4v4H4V4zm6 0h4v4h-4V4zm6 0h4v4h-4V4zM4 10h4v4H4v-4zm6 0h4v4h-4v-4zm6 0h4v4h-4v-4zM4 16h4v4H4v-4zm6 0h4v4h-4v-4zm6 0h4v4h-4v-4z" />
      </svg>
    </button>

    <Transition name="projects-panel">
      <div v-if="open" class="projects-panel" :class="{ 'projects-panel-align-left': align === 'left' }">
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
                {{ project.name === displayedProjectName ? '✓' : '' }}
              </span>
              {{ project.ui_label ?? project.name }}
            </button>
          </li>
        </ul>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.projects-menu {
  position: relative;
}

.projects-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  padding: 0;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.projects-btn:hover {
  background: #4a6fa5;
  color: white;
}

.projects-btn-disabled,
.projects-btn-disabled:hover {
  border-color: #ccc;
  background: #f0f0f0;
  color: #999;
  cursor: default;
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

.projects-panel-enter-active,
.projects-panel-leave-active {
  transition: opacity 0.15s ease;
}

.projects-panel-enter-from,
.projects-panel-leave-to {
  opacity: 0;
}

.projects-panel-align-left {
  right: auto;
  left: 0;
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
  color: #4a6fa5;
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
</style>