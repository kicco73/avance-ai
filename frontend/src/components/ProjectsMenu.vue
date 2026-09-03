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
  align: { type: String, default: 'right' },
  // Live chat's own two extra rows (New/Close session) above the usual
  // project list, with a divider between — opt-in since ManageUsersView.
  // vue/LabelProjectView.vue reuse this same dropdown as a plain project
  // picker with no session of its own to act on.
  sessionActions: { type: Boolean, default: false },
  // Grays out "Close session" the same way projects.length === 0 already
  // grays out the button itself — there's no open session left to close.
  closeSessionDisabled: { type: Boolean, default: false }
})

const emit = defineEmits([
  'select',
  'download',
  'new-session',
  'close-session'
])

const open = ref(false)
const loading = ref(false)
// {id, is_paused, ui_label}[] — ui_label is shown in place of the raw
// id wherever declared.
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

function selectNewSession() {
  open.value = false
  emit('new-session')
}

function selectCloseSession() {
  if (props.closeSessionDisabled) return
  open.value = false
  emit('close-session')
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
      :title="sessionActions ? 'Session and applications' : 'Current project'"
      @click="toggle"
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M4 4h4v4H4V4zm6 0h4v4h-4V4zm6 0h4v4h-4V4zM4 10h4v4H4v-4zm6 0h4v4h-4v-4zm6 0h4v4h-4v-4zM4 16h4v4H4v-4zm6 0h4v4h-4v-4zm6 0h4v4h-4v-4z" />
      </svg>
    </button>

    <Transition name="projects-panel">
      <div v-if="open" class="projects-panel" :class="{ 'projects-panel-align-left': align === 'left' }">
        <template v-if="sessionActions">
          <ul class="projects-list">
            <li>
              <button type="button" class="projects-item projects-session-item" @click="selectNewSession">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 4a1 1 0 0 1 1 1v6h6a1 1 0 1 1 0 2h-6v6a1 1 0 1 1-2 0v-6H5a1 1 0 1 1 0-2h6V5a1 1 0 0 1 1-1z"/></svg>
                <span>New session</span>
              </button>
            </li>
            <li>
              <button
                type="button"
                class="projects-item projects-session-item"
                :disabled="closeSessionDisabled"
                @click="selectCloseSession"
              >
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm3.54 12.12-1.42 1.42L12 13.41l-2.12 2.13-1.42-1.42L10.59 12 8.46 9.88l1.42-1.42L12 10.59l2.12-2.13 1.42 1.42L13.41 12l2.13 2.12z"/></svg>
                <span>Close session</span>
              </button>
            </li>
          </ul>
          <div class="projects-menu-divider"></div>
        </template>

        <p v-if="loading" class="projects-status">Loading…</p>

        <ul v-else class="projects-list">
          <li
            v-for="project in projects"
            :key="project.id"
            class="project-entry"
          >
            <button
              class="projects-item"
              :title="project.ui_label ?? project.id"
              @click="selectProject(project.id)"
            >
              <span class="projects-item-check">
                {{ project.id === displayedProjectName ? '✓' : '' }}
              </span>
              <span class="projects-item-label">{{ project.ui_label ?? project.id }}</span>
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
  min-width: 240px;
  max-width: 320px;
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
  display: flex;
  align-items: center;
  gap: 0.4rem;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.9rem;
  color: #4a6fa5;
}

.projects-item-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.projects-item:hover:not(:disabled) {
  background: #f0f4fa;
}

.projects-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.projects-session-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.projects-session-item svg {
  flex-shrink: 0;
}

.projects-menu-divider {
  height: 1px;
  margin: 0.3rem 0;
  background: #eee;
}

.projects-item-check {
  display: inline-block;
  flex-shrink: 0;
  width: 1.1rem;
  color: #2e7d32;
  font-weight: 600;
}

.project-entry + .project-entry {
  border-top: 1px solid #eee;
}
</style>