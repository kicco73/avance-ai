<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getMetrics, getProjects, getUsers } from '../../api.js'
import DocInfoButton from '../DocInfoButton.vue'
import ErrorBanner from '../ErrorBanner.vue'
import ProjectsMenu from '../ProjectsMenu.vue'
import Inspector from '../inspector/Inspector.vue'
import InspectorUserInfoCard from '../inspector/InspectorUserInfoCard.vue'
import MetricDetail from '../inspector/MetricDetail.vue'

const emit = defineEmits(['close'])

const rows = ref([])
const loading = ref(true)
const selectedUserId = ref(null)

const selectedUser = computed(() => rows.value.find((row) => row.id === selectedUserId.value) ?? null)

const inspectorTabs = [{ id: 'info', label: 'Info' }]
const inspectorActiveTab = ref('info')

// Left/right panel widths in px, adjusted by dragging their split dividers.
const explorerWidth = ref(260)
const inspectorWidth = ref(300)
const explorerCollapsed = ref(false)
const inspectorCollapsed = ref(false)

// Which divider (if any) is currently being dragged — 'explorer' or
// 'inspector' — read by the single shared onDrag/stopDrag pair below.
let dragTarget = null

function startExplorerDrag(event) {
  dragTarget = 'explorer'
  event.preventDefault()
}

function startInspectorDrag(event) {
  dragTarget = 'inspector'
  event.preventDefault()
}

function onDrag(event) {
  if (dragTarget === 'explorer') {
    explorerWidth.value = Math.min(420, Math.max(160, explorerWidth.value + event.movementX))
  } else if (dragTarget === 'inspector') {
    // The inspector's divider sits on its left edge, so dragging it left
    // (negative movementX) needs to grow the panel, not shrink it.
    inspectorWidth.value = Math.min(560, Math.max(240, inspectorWidth.value - event.movementX))
  }
}

function stopDrag() {
  dragTarget = null
}

async function load() {
  loading.value = true
  try {
    const res = await getUsers()
    rows.value = res.users
  } catch {
  } finally {
    loading.value = false
  }
}

function selectUser(id) {
  selectedUserId.value = id
}

// The central panel's own project picker — deliberately independent of the
// app's active project (see ProjectsMenu.vue's `selectedName` prop): picking
// a project here only drives the statistics below, never `activateProject`.
const statsProjectName = ref(null)
const stats = ref([])
const statsLoading = ref(false)

async function loadStats(projectName, user) {
  if (!projectName || !user) {
    stats.value = []
    return
  }
  statsLoading.value = true
  try {
    stats.value = await getMetrics(projectName, null, true, user.email ?? user.id)
  } catch {
  } finally {
    statsLoading.value = false
  }
}

// Re-runs whenever either the toolbar's project or the Explorer's selected
// user changes — the statistics are that user's own sessions of that project.
watch([statsProjectName, selectedUser], ([projectName, user]) => loadStats(projectName, user))

// Seeds the picker with the app's current active project — a read-only
// lookup (never activateProject), just so the panel isn't stuck on "Select
// a project" when ProjectsMenu's own button already displays one (its
// label falls back to the same active project before any local pick —
// see its `selectedName` prop). Without this, a single-project prototype
// looked like it needed a forced re-click to show anything.
async function loadInitialProject() {
  try {
    const res = await getProjects()
    if (statsProjectName.value == null) statsProjectName.value = res.active
  } catch {
  }
}

onMounted(() => {
  load()
  loadInitialProject()
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', stopDrag)
})

onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', stopDrag)
})

defineExpose({ refresh: load })
</script>

<template>
  <div class="manage-users-overlay">
    <div class="manage-users-header">
      <h2>Users</h2>
      <div class="manage-users-header-actions">
        <button class="close-btn" @click="emit('close')">Back</button>
      </div>
    </div>

    <ErrorBanner />

    <div class="manage-users-body">
      <div
        class="manage-users-explorer"
        :class="{ 'manage-users-explorer-collapsed': explorerCollapsed }"
        :style="!explorerCollapsed ? { width: explorerWidth + 'px' } : null"
      >
        <div class="manage-users-explorer-header">
          <span v-show="!explorerCollapsed" class="manage-users-explorer-title">User Explorer</span>
          <button
            class="collapse-toggle-btn"
            :title="explorerCollapsed ? 'Expand explorer' : 'Collapse explorer'"
            @click="explorerCollapsed = !explorerCollapsed"
          >{{ explorerCollapsed ? '▸' : '◂' }}</button>
        </div>

        <template v-if="!explorerCollapsed">
          <p v-if="loading" class="manage-users-status">Loading…</p>
          <p v-else-if="!rows.length" class="manage-users-status">No users yet.</p>

          <ul v-else class="manage-users-list">
            <li v-for="row in rows" :key="row.id">
              <button
                type="button"
                class="manage-users-row"
                :class="{ 'manage-users-row-selected': selectedUserId === row.id }"
                @click="selectUser(row.id)"
              >
                <span class="manage-users-row-name">{{ row.name ?? row.email ?? row.id }}</span>
                <span class="manage-users-row-sublabel">{{ row.email ?? row.id }}</span>
              </button>
            </li>
          </ul>
        </template>
      </div>

      <div v-if="!explorerCollapsed" class="split-divider" @mousedown="startExplorerDrag"></div>

      <div class="manage-users-main">
        <div class="manage-users-main-toolbar">
          <span class="manage-users-main-toolbar-label">Statistics for</span>
          <ProjectsMenu :selected-name="statsProjectName" @select="statsProjectName = $event" />
        </div>

        <div class="manage-users-stats">
          <p v-if="!statsProjectName" class="manage-users-stats-status">Select a project to see its statistics.</p>
          <p v-else-if="!selectedUser" class="manage-users-stats-status">Select a user to see their statistics.</p>
          <template v-else>
            <div class="manage-users-stats-header">
              <h3 class="manage-users-stats-title">Statistics</h3>
              <DocInfoButton doc-name="metrics" title="Core metrics" />
            </div>
            <p v-if="statsLoading" class="manage-users-stats-status">Loading…</p>
            <p v-else-if="!stats.length" class="manage-users-stats-status">No metrics computed yet.</p>
            <div v-else class="manage-users-stats-list">
              <MetricDetail
                v-for="metric in stats"
                :key="metric.name"
                :label="metric.ui_label || metric.name"
                :value="metric.value"
                :description="metric.ui_description"
              />
            </div>
          </template>
        </div>
      </div>

      <div v-if="!inspectorCollapsed" class="split-divider" @mousedown="startInspectorDrag"></div>

      <div
        class="manage-users-inspector"
        :class="{ 'manage-users-inspector-collapsed': inspectorCollapsed }"
        :style="!inspectorCollapsed ? { width: inspectorWidth + 'px' } : null"
      >
        <Inspector :tabs="inspectorTabs" v-model:active-tab="inspectorActiveTab" v-model:collapsed="inspectorCollapsed">
          <template #tab-info>
            <InspectorUserInfoCard :user="selectedUser" />
          </template>
        </Inspector>
      </div>
    </div>
  </div>
</template>

<style scoped>
.manage-users-overlay {
  position: fixed;
  inset: 0;
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.manage-users-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.manage-users-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.manage-users-header-actions {
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

.manage-users-body {
  flex: 1;
  display: flex;
  min-height: 0;
  padding: 1rem;
}

.split-divider {
  flex-shrink: 0;
  width: 6px;
  margin: 0 0.4rem;
  border-radius: 3px;
  background: transparent;
  cursor: col-resize;
}

.split-divider:hover {
  background: #dbe4f0;
}

.manage-users-explorer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
  transition: width 0.15s ease;
}

.manage-users-explorer-collapsed {
  width: 2.4rem !important;
}

.manage-users-explorer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid #ddd;
  background: #f7f8fa;
  flex-shrink: 0;
}

.manage-users-explorer-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.collapse-toggle-btn {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #666;
  cursor: pointer;
  font-size: 0.9rem;
}

.collapse-toggle-btn:hover {
  background: #eee;
}

.manage-users-status {
  margin: 0;
  padding: 0.6rem;
  font-size: 0.85rem;
  color: #666;
}

.manage-users-list {
  list-style: none;
  margin: 0;
  padding: 0.4rem;
  overflow-y: auto;
  flex: 1;
}

.manage-users-row {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.1rem;
  text-align: left;
  padding: 0.45rem 0.6rem;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
}

.manage-users-row:hover {
  background: #eef2f8;
}

.manage-users-row-selected {
  background: #e3ebf7;
}

.manage-users-row-name {
  font-size: 0.85rem;
  font-weight: 600;
  color: #222;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.manage-users-row-sublabel {
  font-size: 0.72rem;
  color: #777;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.manage-users-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.manage-users-main-toolbar {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #ddd;
  background: #f7f8fa;
  flex-shrink: 0;
}

.manage-users-main-toolbar-label {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.manage-users-stats {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
}

.manage-users-stats-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.4rem;
  margin-bottom: 0.75rem;
  flex-shrink: 0;
}

.manage-users-stats-title {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
  color: #333;
}

.manage-users-stats-status {
  margin: 0;
  padding: 0.75rem 0;
  font-size: 0.9rem;
  color: #666;
}

.manage-users-stats-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.manage-users-inspector {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
  transition: width 0.15s ease;
}

.manage-users-inspector-collapsed {
  width: 2.4rem !important;
}
</style>
