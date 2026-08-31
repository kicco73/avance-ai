<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getMetrics, getProjectGraph, getProjects, getUserLatestSignals, getUsers, putUserRole } from '../../api.js'
import { valuesToSignalValues } from '../../testTimeline.js'
import { confirmDialog } from '../../dialogStore.js'
import { roleSatisfies } from '../../roles.js'
import DocInfoButton from '../DocInfoButton.vue'
import ProjectsMenu from '../ProjectsMenu.vue'
import SettingsMenu from './SettingsMenu.vue'
import ProfileMenu from '../ProfileMenu.vue'
import InspectorSignalsTab from '../inspector/InspectorSignalsTab.vue'
import InspectorUserInfoCard from '../inspector/InspectorUserInfoCard.vue'
import MetricDetail from '../inspector/MetricDetail.vue'
import MetricsTrendsChart from './MetricsTrendsChart.vue'
import TimelineChart from './TimelineChart.vue'

const props = defineProps({
  currentUserRole: { type: String, default: null },
  // ProfileMenu.vue's own avatar/name — App.vue already fetched this once
  // during boot, passed straight through so this view can show the same
  // topbar avatar the main chat screen does.
  profile: { type: Object, default: null }
})

// The Settings-menu ones (manage-projects/manage-users/label-sessions/
// about/download-backup/restore-backup) are a plain pass-through of
// SettingsMenu.vue's own emits; profile/logout are the same pass-through
// of ProfileMenu.vue's own.
const emit = defineEmits([
  'close', 'manage-projects', 'manage-users', 'label-sessions', 'edit-projects', 'about', 'download-backup',
  'restore-backup', 'profile', 'logout'
])

const canEditRole = computed(() => roleSatisfies(props.currentUserRole, 'admin'))

const rows = ref([])
const loading = ref(true)
const selectedUserId = ref(null)

const selectedUser = computed(() => rows.value.find((row) => row.id === selectedUserId.value) ?? null)

const explorerWidth = ref(260)
const explorerCollapsed = ref(false)

let dragging = false

function startExplorerDrag(event) {
  dragging = true
  event.preventDefault()
}

function onDrag(event) {
  if (!dragging) return
  explorerWidth.value = Math.min(420, Math.max(160, explorerWidth.value + event.movementX))
}

function stopDrag() {
  dragging = false
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

async function handleChangeRole(role) {
  const user = selectedUser.value
  if (!user) return
  const ok = await confirmDialog({
    title: 'Change role',
    body: `Change ${user.name ?? user.email}'s role from "${user.role}" to "${role}"?`,
    okLabel: 'Change role'
  })
  if (!ok) return
  try {
    const updated = await putUserRole(user.id, role)
    const index = rows.value.findIndex((row) => row.id === user.id)
    if (index !== -1) rows.value[index] = updated
  } catch {
    // already surfaced via apiFetch
  }
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

const mainTabs = [{ id: 'info', label: 'Info' }, { id: 'signals', label: 'Timeline' }, { id: 'metrics', label: 'Metrics' }]
const activeMainTab = ref('info')

const signalColorMap = ref(null)
const metricColorMap = ref(null)

function metricBadgeColor(name) {
  if (metricColorMap.value == null) return null
  return metricColorMap.value[name] ?? '#9e9e9e'
}

const latestSignals = ref({ last_session: null, session_id: null, values: null })
const latestSignalsLoading = ref(false)
const latestSignalValues = computed(() => valuesToSignalValues(latestSignals.value.values))

const projectGraphNodes = ref([])
const lastSessionStateNode = computed(() => {
  const key = latestSignals.value.last_session?.end_state
  if (key == null) return null
  return projectGraphNodes.value.find((node) => node.state.key === key) ?? null
})

async function loadLatestSignals(projectName, user) {
  if (!projectName || !user) {
    latestSignals.value = { last_session: null, session_id: null, values: null }
    projectGraphNodes.value = []
    return
  }
  latestSignalsLoading.value = true
  try {
    latestSignals.value = await getUserLatestSignals(projectName, user.email ?? user.id)
    projectGraphNodes.value = (await getProjectGraph(projectName, latestSignals.value.last_session?.id)).nodes
  } catch {
    latestSignals.value = { last_session: null, session_id: null, values: null }
    projectGraphNodes.value = []
  } finally {
    latestSignalsLoading.value = false
  }
}

// Re-runs whenever either the toolbar's project or the Explorer's selected
// user changes — the statistics are that user's own sessions of that project.
watch([statsProjectName, selectedUser], ([projectName, user]) => {
  loadStats(projectName, user)
  loadLatestSignals(projectName, user)
})

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
      <button class="back-btn" title="Back" @click="emit('close')">«</button>
      <h2>Users</h2>
      <div class="manage-users-header-actions">
        <ProjectsMenu :selected-name="statsProjectName" @select="statsProjectName = $event" />
        <SettingsMenu
          :role="currentUserRole"
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
        <div class="manage-users-main-tabbar">
          <button
            v-for="tab in mainTabs"
            :key="tab.id"
            type="button"
            class="manage-users-main-tab"
            :class="{ 'manage-users-main-tab-active': activeMainTab === tab.id }"
            @click="activeMainTab = tab.id"
          >{{ tab.label }}</button>
        </div>

        <div v-if="activeMainTab === 'info'" class="manage-users-stats">
          <p v-if="!selectedUser" class="manage-users-stats-status">Select a user to see their info.</p>
          <template v-else>
            <InspectorUserInfoCard :user="selectedUser" large :can-edit-role="canEditRole" @change-role="handleChangeRole" />
            <div v-if="lastSessionStateNode" class="inspector-signal-block manage-users-state-card">
              <div class="inspector-signal-readonly">
                <div class="inspector-signal-header">
                  <span class="inspector-detail-badge inspector-detail-badge-state">State</span>
                  <span class="inspector-signal-name">{{ lastSessionStateNode.state.ui_label || lastSessionStateNode.state.key }}</span>
                </div>
                <span v-if="lastSessionStateNode.state.ui_description" class="inspector-signal-ui_description">{{ lastSessionStateNode.state.ui_description }}</span>
              </div>
            </div>
          </template>
        </div>

        <div v-else-if="activeMainTab === 'signals'" class="manage-users-stats">
          <p v-if="!statsProjectName" class="manage-users-stats-status">Select a project to see timelines.</p>
          <p v-else-if="!selectedUser" class="manage-users-stats-status">Select a user to see their timeline.</p>
          <template v-else>
            <div class="manage-users-trends-block">
              <TimelineChart
                :project-name="statsProjectName"
                :username="selectedUser.email ?? selectedUser.id"
                @colors="signalColorMap = $event"
              />
            </div>
            <p v-if="latestSignalsLoading" class="manage-users-stats-status">Loading…</p>
            <p v-else-if="!latestSignals.last_session" class="manage-users-stats-status">
              This user has no live sessions in this project yet.
            </p>
            <InspectorSignalsTab
              v-else
              :project-name="statsProjectName"
              :signal-values="latestSignalValues"
              :session-id="latestSignals.session_id"
              :signal-colors="signalColorMap"
            />
          </template>
        </div>

        <div v-else class="manage-users-stats">
          <p v-if="!statsProjectName" class="manage-users-stats-status">Select a project to see its statistics.</p>
          <p v-else-if="!selectedUser" class="manage-users-stats-status">Select a user to see their statistics.</p>
          <template v-else>
            <div class="manage-users-trends-block">
              <MetricsTrendsChart
                :project-name="statsProjectName"
                :username="selectedUser.email ?? selectedUser.id"
                @colors="metricColorMap = $event"
              />
            </div>
            <div class="manage-users-stats-header">
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
                :color="metricBadgeColor(metric.name)"
              />
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.manage-users-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  /* Extends past the viewport's own bottom edge on standalone iOS,
     where WebKit bug #301108 leaves a gap there otherwise — see
     index.html's own viewport meta comment and
     useVisualViewport.js's installViewportOvershoot(). 0px, a no-op,
     everywhere else (a plain browser tab, non-iOS, or once Apple fixes
     the bug). */
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  box-sizing: border-box;
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.manage-users-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: calc(0.75rem + var(--safe-area-top)) 1rem 0.75rem;
  border-bottom: 1px solid #ddd;
}

.manage-users-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.manage-users-header-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.back-btn {
  flex-shrink: 0;
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
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}

.back-btn:hover {
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

.manage-users-main-tabbar {
  display: flex;
  gap: 0.25rem;
  padding: 0.5rem 1rem 0;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

.manage-users-main-tab {
  padding: 0.45rem 0.9rem;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: none;
  cursor: pointer;
  font-size: 0.82rem;
  color: #666;
}

.manage-users-main-tab:hover {
  color: #333;
}

.manage-users-main-tab-active {
  color: #2c4d7a;
  font-weight: 600;
  border-bottom-color: #4a6fa5;
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
  justify-content: flex-end;
  gap: 0.4rem;
  margin-bottom: 0.75rem;
  flex-shrink: 0;
}

.manage-users-stats-status {
  margin: 0;
  padding: 0.75rem 0;
  font-size: 0.9rem;
  color: #666;
}

.manage-users-stats-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  align-items: start;
  gap: 0.6rem;
}

.manage-users-state-card {
  margin-top: 0.75rem;
  margin-bottom: 0.75rem;
}

.manage-users-trends-block {
  width: 100%;
  height: 200px;
  max-height: 200px;
  flex-shrink: 0;
  margin-bottom: 1rem;
}

.manage-users-stats :deep(.inspector-signals-section) {
  flex: none;
  overflow: visible;
}

.inspector-signal-block { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-state { background: #4a6fa5; }
.inspector-signal-name { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-signal-ui_description { display: block; margin-top: 0.3rem; font-size: 0.78rem; color: #666; line-height: 1.4; }
</style>
