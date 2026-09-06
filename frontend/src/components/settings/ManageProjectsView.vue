<script setup>
// Settings > Manage projects: one row per project with its three-state
// status (see backend ProjectService._project_status) and revision info.
// The status dot toggles running <-> manually_paused only; 'paused' needs an external fix.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getAppStoreApps, getProjectBrokenWarnings, getProjectFiles, getProjectMetadata, getProjectsRuntimeStatus, projectFileContentUrl, putProjectPause, putProjectResume } from '../../api.js'
import { confirmDialog, customDialog } from '../../dialogStore.js'
import { setCanvasColor, restoreCanvasColor } from '../../canvasColor.js'
import SettingsMenu from './SettingsMenu.vue'
import StatusToggleButton from './StatusToggleButton.vue'
import ProfileMenu from '../ProfileMenu.vue'
import AppHeader from '../AppHeader.vue'
import ProjectDetailPanel from './ProjectDetailPanel.vue'
import ShareProjectDialog from './ShareProjectDialog.vue'
import avanceLogoUrl from '../../assets/avance-logo.png'
import avanceLogoLargeUrl from '../../assets/avance-logo-large.png'

const props = defineProps({
  uploading: { type: Boolean, default: false },
  // 0-100, or null before the first progress chunk has arrived — see
  // SessionsTree.vue's identical importProgress for the same reasoning.
  uploadProgress: { type: Number, default: null },
  uploadProjectId: { type: String, default: null },
  uploadIconReady: { type: Boolean, default: false },
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
// The Settings-menu ones (manage-users/manage-services/about) are a
// plain pass-through of SettingsMenu.vue's own emits; profile/logout are
// the same pass-through of ProfileMenu.vue's own.
const emit = defineEmits([
  'new-project', 'upload', 'delete', 'edit', 'label', 'download',
  'manage-users', 'manage-services', 'app-store', 'about',
  'home', 'profile', 'logout'
])

const envTag = (() => {
  const host = window.location.hostname
  if (host === 'localhost') return { label: 'DEV', className: 'manage-projects-env-tag-dev' }
  if (host.startsWith('staging')) return { label: 'STAGING', className: 'manage-projects-env-tag-staging' }
  return { label: 'PROD', className: 'manage-projects-env-tag-prod' }
})()

const rows = ref([])
const loading = ref(true)
const searchQuery = ref('')
// id of the project with a pause/resume request in flight; disables
// only that row's button.
const togglingProject = ref(null)
// id -> { id, ui_label, ui_description }, filled in after the runtime-status
// list loads — the project detail card's own content (title/id/description).
const metadataById = ref({})
// id -> its resolved 'aspect/icon.<ext>' archive name (whatever image
// extension it was uploaded with — see findIconFile), filled in alongside
// metadataById. Absent (not just falsy) for a project with no icon file.
const iconFileById = ref({})
// id -> true once that project's icon request has failed (e.g. deleted
// after iconFileById was resolved), same fallback idiom as ProfileMenu.vue's
// avatar image.
const iconFailedById = ref({})

// Matches whatever extension a project's icon was actually uploaded
// with — the backend accepts any of these under aspect/ (see
// backend/src/project/archive/layout.py's IMAGE_CONTENT_TYPE_BY_EXTENSION),
// so the icon file itself is never assumed to be .png.
const ICON_FILE_RE = /^aspect\/icon\.(png|jpe?g|gif|webp|svg)$/i
function findIconFile(files) {
  return files.find((name) => ICON_FILE_RE.test(name)) ?? null
}

const addMenuOpen = ref(false)
// "Broken project" warnings counter/list — a durable audit trail (see
// getProjectBrokenWarnings) distinct from each row's own live `broken`
// field, which disappears the moment that project is fixed.
const warningsMenuOpen = ref(false)
const warnings = ref([])

const appStoreAppById = ref({})
const selectedProjectId = ref(null)
const selectedAppStoreApp = computed(() => appStoreAppById.value[selectedProjectId.value] ?? null)
const selectedRow = computed(() => rows.value.find((row) => row.id === selectedProjectId.value) ?? null)

function selectProject(id) {
  selectedProjectId.value = id
}

async function loadAppStoreApps() {
  try {
    const { apps } = await getAppStoreApps()
    appStoreAppById.value = Object.fromEntries(apps.map((app) => [app.id, app]))
  } catch {
    // already surfaced via apiFetch
  }
}

const headerEl = ref(null)
const headerLeftEl = ref(null)
const headerActionsEl = ref(null)
const headerWidth = ref(0)
const headerLeftWidth = ref(0)
const headerActionsWidth = ref(0)
const logoWidth = ref(0)
let headerResizeObserver = null

const logoFits = computed(() => !logoWidth.value || headerWidth.value - headerLeftWidth.value - headerActionsWidth.value >= logoWidth.value)

function setLogoBtnEl(el) {
  if (!el || logoWidth.value > 0) return
  nextTick(() => { logoWidth.value = el.getBoundingClientRect().width })
}

function handleHeaderResize(entries) {
  for (const entry of entries) {
    const width = entry.contentRect.width
    if (entry.target === headerEl.value.el) headerWidth.value = width
    else if (entry.target === headerLeftEl.value) headerLeftWidth.value = width
    else if (entry.target === headerActionsEl.value) headerActionsWidth.value = width
  }
}

function toggleAddMenu() {
  addMenuOpen.value = !addMenuOpen.value
}

async function loadWarnings() {
  try {
    const { warnings: rows } = await getProjectBrokenWarnings()
    warnings.value = rows
  } catch {
    // already surfaced via apiFetch
  }
}

function toggleWarningsMenu() {
  warningsMenuOpen.value = !warningsMenuOpen.value
  if (warningsMenuOpen.value) loadWarnings()
}

function formatWarningTimestamp(timestamp) {
  return new Date(timestamp).toLocaleString()
}

function selectNewProject() {
  addMenuOpen.value = false
  emit('new-project')
}

function selectUploadProject() {
  addMenuOpen.value = false
  emit('upload')
}

function handleDocumentClick(event) {
  if (addMenuOpen.value && !event.target.closest('.manage-projects-add-menu')) {
    addMenuOpen.value = false
  }
  if (warningsMenuOpen.value && !event.target.closest('.manage-projects-warnings-menu')) {
    warningsMenuOpen.value = false
  }
}

async function loadMetadata(ids) {
  const results = await Promise.allSettled(ids.map((id) => getProjectMetadata(id)))
  results.forEach((result, i) => {
    if (result.status === 'fulfilled') metadataById.value[ids[i]] = result.value.project
  })
}

async function loadIcons(ids) {
  const results = await Promise.allSettled(ids.map((id) => getProjectFiles(id)))
  results.forEach((result, i) => {
    if (result.status !== 'fulfilled') return
    const iconFile = findIconFile(result.value.files)
    if (iconFile) iconFileById.value[ids[i]] = iconFile
  })
}

async function load() {
  loading.value = true
  try {
    const res = await getProjectsRuntimeStatus()
    rows.value = res.projects
    loadMetadata(rows.value.map((row) => row.id))
    loadIcons(rows.value.map((row) => row.id))
    loadAppStoreApps()
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

function projectTitle(id) {
  return metadataById.value[id]?.ui_label || id
}

function projectDescription(id) {
  return metadataById.value[id]?.ui_description || null
}

const visibleRows = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return rows.value
  return rows.value.filter((row) => {
    const label = projectTitle(row.id).toLowerCase()
    const description = (projectDescription(row.id) || '').toLowerCase()
    return label.includes(q) || description.includes(q)
  })
})

const uploadRowTitle = computed(() => (props.uploadProgress == null ? 'Uploading' : 'Installing'))

const uploadIconFailed = ref(false)
const uploadIconLoaded = ref(false)
// The uploaded project's own resolved 'aspect/icon.<ext>' name (see
// findIconFile) — looked up once uploadIconReady fires, since the
// extension isn't known in advance.
const uploadIconFile = ref(null)
watch(() => props.uploading, (value) => {
  if (value) {
    uploadIconFailed.value = false
    uploadIconLoaded.value = false
    uploadIconFile.value = null
  }
})
watch(() => props.uploadIconReady, async (ready) => {
  if (!ready || !props.uploadProjectId) return
  let iconFile = null
  try {
    const { files } = await getProjectFiles(props.uploadProjectId)
    iconFile = findIconFile(files)
  } catch {
    return // already surfaced via apiFetch
  }
  if (!iconFile) return // no icon in this upload — the fallback logo stays put
  uploadIconFile.value = iconFile
  const preload = new Image()
  preload.onload = () => { uploadIconLoaded.value = true }
  preload.onerror = () => { uploadIconFailed.value = true }
  preload.src = projectFileContentUrl(props.uploadProjectId, iconFile)
})

function replaceRow(updated) {
  const idx = rows.value.findIndex((r) => r.id === updated.id)
  if (idx !== -1) rows.value[idx] = updated
}

async function toggleStatus(row) {
  if (row.status === 'paused') return // the automatic case — nothing to toggle here
  if (row.status === 'running') {
    const ok = await confirmDialog({
      title: 'Pause project',
      body: `Pause "${row.id}"? Live chat on this project will show a maintenance screen until it's resumed.`,
      okLabel: 'Pause'
    })
    if (!ok) return
  }
  togglingProject.value = row.id
  try {
    const updated = row.status === 'running'
      ? await putProjectPause(row.id)
      : await putProjectResume(row.id)
    replaceRow(updated)
  } catch {
    // already surfaced via apiFetch
  } finally {
    togglingProject.value = null
  }
}

// App.vue's delete handler has no confirm of its own, so this is the
// one place that asks before deleting.
async function selectDelete(id) {
  const ok = await confirmDialog({
    title: 'Delete project',
    body: `Delete project "${id}"? This cannot be undone.`,
    okLabel: 'Delete',
    danger: true
  })
  if (!ok) return
  emit('delete', id)
}

function selectEdit(id) {
  emit('edit', id)
}

function selectLabelSessions(id) {
  emit('label', id)
}

function selectDownload(id) {
  emit('download', id)
}

function selectShare(id) {
  customDialog({ component: ShareProjectDialog, props: { projectId: id, uiLabel: projectTitle(id) } })
}

function statusTitle(row) {
  if (row.status === 'running') return 'Running'
  if (row.status === 'manually_paused') return 'Manually paused'
  return row.paused_reason ?? 'Paused'
}

// Every view this screen can push over itself (Edit, Label sessions,
// Manage users, Profile) is white too, so this one setCanvasColor covers
// all of them — the sole exception is the chat, which restores its own
// prior value on unmount (see LiveChatWindow.vue), leaving this one
// intact underneath rather than fighting it.
let previousCanvasColor = ''

onMounted(() => {
  load()
  loadWarnings()
  previousCanvasColor = setCanvasColor('#ffffff')
  headerResizeObserver = new ResizeObserver(handleHeaderResize)
  headerResizeObserver.observe(headerEl.value.el)
  headerResizeObserver.observe(headerLeftEl.value)
  headerResizeObserver.observe(headerActionsEl.value)
  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  restoreCanvasColor(previousCanvasColor)
  headerResizeObserver?.disconnect()
  document.removeEventListener('click', handleDocumentClick)
})

defineExpose({ refresh: load })
</script>

<template>
  <div class="manage-projects-overlay">
    <AppHeader ref="headerEl">
      <template #left>
        <div class="manage-projects-header-side" ref="headerLeftEl">
          <SettingsMenu
            :role="role"
            @manage-users="emit('manage-users')"
            @manage-services="emit('manage-services')"
            @app-store="emit('app-store')"
          />
          <div class="manage-projects-add-menu">
            <button
              type="button"
              class="manage-projects-action-btn"
              title="Add project"
              @click="toggleAddMenu"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                <path d="M12 4a1 1 0 0 1 1 1v6h6a1 1 0 1 1 0 2h-6v6a1 1 0 1 1-2 0v-6H5a1 1 0 1 1 0-2h6V5a1 1 0 0 1 1-1z" />
              </svg>
            </button>
            <Transition name="manage-projects-add-panel">
              <div v-if="addMenuOpen" class="manage-projects-add-panel">
                <ul class="manage-projects-add-list">
                  <li>
                    <button type="button" class="manage-projects-add-item" @click="selectNewProject">
                      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                        <path d="M12 4a1 1 0 0 1 1 1v6h6a1 1 0 1 1 0 2h-6v6a1 1 0 1 1-2 0v-6H5a1 1 0 1 1 0-2h6V5a1 1 0 0 1 1-1z" />
                      </svg>
                      <span>New project</span>
                    </button>
                  </li>
                  <li>
                    <button type="button" class="manage-projects-add-item" @click="selectUploadProject">
                      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                        <path d="M12 21a1 1 0 0 1-1-1v-9.59l-2.3 2.3a1 1 0 1 1-1.4-1.42l4-4a1 1 0 0 1 1.4 0l4 4a1 1 0 1 1-1.4 1.42l-2.3-2.3V20a1 1 0 0 1-1 1zM5 5a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H6a1 1 0 0 1-1-1z" />
                      </svg>
                      <span>Import project...</span>
                    </button>
                  </li>
                </ul>
              </div>
            </Transition>
          </div>
          <div v-if="warnings.length" class="manage-projects-warnings-menu">
            <button
              type="button"
              class="manage-projects-action-btn manage-projects-warnings-btn"
              title="Broken project warnings"
              @click="toggleWarningsMenu"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                <path d="M12 2 1 21h22L12 2zm0 6.5 6.53 11.5H5.47L12 8.5zM11 11v4h2v-4h-2zm0 5.5v2h2v-2h-2z" />
              </svg>
              <span class="manage-projects-warnings-count">{{ warnings.length }}</span>
            </button>
            <Transition name="manage-projects-add-panel">
              <div v-if="warningsMenuOpen" class="manage-projects-add-panel manage-projects-warnings-panel">
                <p class="manage-projects-warnings-title">Broken project warnings</p>
                <ul class="manage-projects-warnings-list">
                  <li v-for="warning in warnings" :key="warning.id" class="manage-projects-warnings-item">
                    <span class="manage-projects-warnings-item-project">{{ projectTitle(warning.project_id) }}</span>
                    <span class="manage-projects-warnings-item-time">{{ formatWarningTimestamp(warning.timestamp) }}</span>
                    <span class="manage-projects-warnings-item-message">{{ warning.message }}</span>
                  </li>
                </ul>
              </div>
            </Transition>
          </div>
          <span class="manage-projects-env-tag" :class="envTag.className">{{ envTag.label }}</span>
        </div>
      </template>
      <template #center>
        <button
          v-if="logoFits"
          type="button"
          class="manage-projects-header-logo-btn"
          :ref="setLogoBtnEl"
          title="About Avance"
          @click="emit('about')"
        >
          <img :src="avanceLogoLargeUrl" alt="Avance" class="manage-projects-header-logo" />
        </button>
      </template>
      <template #right>
        <div class="manage-projects-header-actions" ref="headerActionsEl">
          <ProfileMenu :profile="profile" @home="emit('home')" @profile="emit('profile')" @logout="emit('logout')" />
        </div>
      </template>
    </AppHeader>

    <div class="manage-projects-body">
      <div class="manage-projects-list">
      <input
        v-model="searchQuery"
        type="search"
        class="manage-projects-search"
        placeholder="Search apps..."
      />
      <div class="manage-projects-table-wrap">
      <p v-if="loading" class="manage-projects-status">Loading…</p>
      <p v-else-if="!rows.length && !uploading" class="manage-projects-status">No projects yet.</p>
      <p v-else-if="!visibleRows.length" class="manage-projects-status">No projects found.</p>

      <table v-else class="manage-projects-table">
        <tbody>
          <tr
            v-for="row in visibleRows"
            :key="row.id"
            class="manage-projects-row"
            :class="{ 'manage-projects-row-selected': selectedProjectId === row.id }"
            @click="selectProject(row.id)"
            @dblclick="selectEdit(row.id)"
          >
            <td class="manage-projects-col-status-actions">
              <div class="manage-projects-status-actions-row">
                <StatusToggleButton
                  :status="row.status"
                  :disabled="row.status === 'paused' || togglingProject === row.id"
                  :title="statusTitle(row)"
                  @click="toggleStatus(row)"
                />
              </div>
            </td>
            <td class="manage-projects-name">
              <div class="project-card">
                <span class="project-card-icon-btn">
                  <img
                    v-if="iconFileById[row.id] && !iconFailedById[row.id]"
                    :src="projectFileContentUrl(row.id, iconFileById[row.id])"
                    class="project-card-icon"
                    alt=""
                    @error="iconFailedById[row.id] = true"
                  />
                  <img v-else :src="avanceLogoUrl" class="project-card-fallback-logo" alt="" />
                </span>
                <div class="project-card-body">
                  <span class="project-card-title-row">
                    <span class="project-card-title">{{ projectTitle(row.id) }}</span>
                    <span v-if="row.broken?.published" class="project-card-broken" :title="row.broken.published">broken</span>
                    <span v-if="row.broken?.draft" class="project-card-draft-broken" :title="row.broken.draft">draft broken</span>
                    <span
                      v-if="row.build_warnings?.length"
                      class="project-card-build-warnings"
                      :title="row.build_warnings.join('\n')"
                    >{{ row.build_warnings.length }} warning{{ row.build_warnings.length === 1 ? '' : 's' }}</span>
                  </span>
                  <span v-if="projectDescription(row.id)" class="project-card-desc">{{ projectDescription(row.id) }}</span>
                </div>
              </div>
            </td>
          </tr>
          <tr v-if="uploading">
            <td class="manage-projects-col-status-actions"></td>
            <td class="manage-projects-name">
              <div class="project-card project-card-placeholder">
                <span class="project-card-icon-wrap">
                  <img :src="avanceLogoUrl" class="project-card-fallback-logo manage-projects-upload-icon-glow" alt="" />
                  <Transition name="manage-projects-upload-icon">
                    <img
                      v-if="uploadIconLoaded && !uploadIconFailed"
                      :src="projectFileContentUrl(uploadProjectId, uploadIconFile)"
                      class="project-card-icon manage-projects-upload-icon-glow"
                      alt=""
                    />
                  </Transition>
                </span>
                <span class="project-card-body">
                  <span class="project-card-title-row">
                    <span class="project-card-title">{{ uploadRowTitle }}</span>
                  </span>
                  <span class="manage-projects-upload-bar-track">
                    <span class="manage-projects-upload-bar-fill" :style="{ width: `${uploadProgress ?? 0}%` }"></span>
                  </span>
                </span>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
      </div>

      <div class="manage-projects-preview">
        <ProjectDetailPanel
          v-if="selectedAppStoreApp"
          :key="selectedAppStoreApp.id"
          :app="selectedAppStoreApp"
          :published-revision="selectedRow?.published_revision ?? null"
          @edit="selectEdit"
          @label="selectLabelSessions"
          @download="selectDownload"
          @share="selectShare"
          @delete="selectDelete"
        />
        <p v-else-if="selectedProjectId" class="manage-projects-status">This project hasn't been published yet — no preview available.</p>
        <p v-else class="manage-projects-status">Select a project to see its details.</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.manage-projects-overlay {
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
  /* Side edges only — same split as LiveChatWindow.vue's own
     .live-chat-window (see its comment): top/bottom are reserved by
     .manage-projects-header/.manage-projects-body instead, the elements
     whose background actually needs to extend behind the notch/home
     indicator rather than showing this white fallback through a gap.
     box-sizing so the padding shrinks the box instead of sitting outside
     it. */
  box-sizing: border-box;
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.manage-projects-header-side {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.manage-projects-header-logo-btn {
  display: flex;
  align-items: center;
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
}

.manage-projects-header-logo {
  height: 1.6rem;
  width: auto;
}

.manage-projects-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.manage-projects-action-btn {
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

.manage-projects-action-btn:hover {
  background: #4a6fa5;
  color: white;
}

.manage-projects-add-menu {
  position: relative;
}

.manage-projects-env-tag {
  display: inline-flex;
  align-items: center;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.03em;
  color: white;
}

.manage-projects-env-tag-dev {
  background: #2e7d32;
}

.manage-projects-env-tag-staging {
  background: #e65100;
}

.manage-projects-env-tag-prod {
  background: #c62828;
}

.manage-projects-add-panel {
  position: absolute;
  top: calc(100% + 0.4rem);
  left: 0;
  min-width: 12rem;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 100;
  overflow: hidden;
  transform-origin: top left;
}

.manage-projects-add-panel-enter-active,
.manage-projects-add-panel-leave-active {
  transition: opacity 0.15s ease;
}

.manage-projects-add-panel-enter-from,
.manage-projects-add-panel-leave-to {
  opacity: 0;
}

.manage-projects-add-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
}

.manage-projects-add-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  color: #4a6fa5;
}

.manage-projects-add-item:hover {
  background: #f0f4fa;
}

.manage-projects-add-item svg {
  flex-shrink: 0;
  color: #4a6fa5;
}

.manage-projects-warnings-menu {
  position: relative;
}

.manage-projects-warnings-btn {
  position: relative;
  border-color: #c0392b;
  color: #c0392b;
}

.manage-projects-warnings-btn:hover {
  background: #c0392b;
  color: white;
}

.manage-projects-warnings-count {
  position: absolute;
  top: -0.35rem;
  right: -0.35rem;
  min-width: 1.1rem;
  height: 1.1rem;
  padding: 0 0.25rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: #c0392b;
  color: white;
  font-size: 0.65rem;
  font-weight: 700;
  line-height: 1;
}

.manage-projects-warnings-panel {
  min-width: 20rem;
  max-width: 26rem;
}

.manage-projects-warnings-title {
  margin: 0;
  padding: 0.6rem 0.9rem 0.4rem;
  font-size: 0.75rem;
  font-weight: 700;
  color: #333;
  border-bottom: 1px solid #eee;
}

.manage-projects-warnings-list {
  list-style: none;
  margin: 0;
  padding: 0.2rem 0;
  max-height: 18rem;
  overflow-y: auto;
}

.manage-projects-warnings-item {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  padding: 0.5rem 0.9rem;
  border-bottom: 1px solid #f3f3f3;
}

.manage-projects-warnings-item:last-child {
  border-bottom: none;
}

.manage-projects-warnings-item-project {
  font-size: 0.8rem;
  font-weight: 600;
  color: #c0392b;
}

.manage-projects-warnings-item-time {
  font-size: 0.65rem;
  color: #999;
}

.manage-projects-warnings-item-message {
  font-size: 0.75rem;
  color: #555;
  white-space: pre-wrap;
  word-break: break-word;
}

.manage-projects-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 1rem;
  padding: 0 1rem 0 0;
}

.manage-projects-list {
  flex: none;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding-top: 20px;
  background: rgba(255, 255, 255, 0.65);
}

.manage-projects-search {
  flex-shrink: 0;
  box-sizing: border-box;
  width: 100%;
  padding: 0.5rem 0.7rem;
  border: 1px solid #ccc;
  border-radius: 6px;
  font-size: 0.85rem;
}

.manage-projects-table-wrap {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* Reserves the home indicator / gesture nav bar for the *scrollable
     content*, not the outer box — this is the flex:1 middle section,
     sized by flex distribution rather than its own content, so a
     content-box padding here doesn't grow past what flex already gave
     it. What it does do is keep the last table row (or the "Add" panel
     dropdown) from sitting bottom-flush against that edge, reachable
     only by scrolling past it, once the list is long enough to scroll
     at all. */
  padding-bottom: var(--safe-area-bottom);
}

.manage-projects-row {
  cursor: pointer;
}

.manage-projects-row-selected .project-card {
  border-color: #4a6fa5;
  background: #eef3fa;
}

.manage-projects-preview {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  overflow-y: auto;
  padding: 20px 0 var(--safe-area-bottom);
}

.manage-projects-status {
  margin: 0;
  padding: 0.75rem 0 0.75rem 20px;
  font-size: 0.9rem;
  color: #666;
}

.manage-projects-table {
  /* No width: 100% — with table-layout: fixed and only two columns of
     fixed width now (the play/pause button and the card, see below),
     a stretched table would proportionally grow both on a wide screen
     instead of just leaving the rest of the row unused. Auto-width
     shrinks the table to those columns' own combined width and keeps
     it left-aligned, same as any other block-level element. */
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 0.88rem;
}

.manage-projects-name {
  width: 320px;
  padding: 0 0.75rem 0 0;
}

.manage-projects-table td {
  padding: 0.5rem 0.75rem;
  vertical-align: middle;
}

.manage-projects-col-status-actions {
  width: 2.88rem;
  padding-left: 0;
  padding-right: 0;
}

.manage-projects-status-actions-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
  width: 100%;
}

.project-card {
  box-sizing: border-box;
  display: flex;
  align-items: stretch;
  gap: 0.6rem;
  width: 100%;
  max-width: 320px;
  padding: 0.5rem 0.7rem;
  border: 1px solid #eee;
  border-radius: 8px;
  background: #fafafa;
}

.project-card-icon-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border-radius: 15px;
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
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
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

.project-card-broken,
.project-card-draft-broken,
.project-card-build-warnings {
  flex-shrink: 0;
  padding: 0.05rem 0.4rem;
  border-radius: 999px;
  font-size: 0.65rem;
  font-weight: 600;
  cursor: help;
}

.project-card-broken {
  background: #fdecea;
  color: #c0392b;
}

.project-card-draft-broken,
.project-card-build-warnings {
  background: #fdf1e3;
  color: #b06a00;
}

.project-card-desc {
  font-size: 0.75rem;
  color: #777;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.project-card-placeholder {
  cursor: default;
  pointer-events: none;
}

.project-card-icon-wrap {
  position: relative;
  flex-shrink: 0;
  width: 65px;
  height: 65px;
}

.project-card-icon-wrap .project-card-fallback-logo,
.project-card-icon-wrap .project-card-icon {
  position: absolute;
  inset: 0;
}

.manage-projects-upload-icon-enter-active {
  transition: opacity 0.4s ease;
}

.manage-projects-upload-icon-enter-from {
  opacity: 0;
}

.manage-projects-upload-icon-glow {
  animation: manage-projects-upload-icon-glow-pulse 1.8s ease-in-out infinite;
}

@keyframes manage-projects-upload-icon-glow-pulse {
  0%, 100% {
    filter: drop-shadow(0 0 2px rgba(74, 111, 165, 0.35));
  }
  50% {
    filter: drop-shadow(0 0 10px rgba(74, 111, 165, 0.9));
  }
}

.manage-projects-upload-bar-track {
  display: block;
  width: 100%;
  margin-top: 0.3rem;
  height: 8px;
  border-radius: 999px;
  background: #eee;
  overflow: hidden;
}

.manage-projects-upload-bar-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: #4a6fa5;
  transition: width 0.3s ease;
}
</style>
