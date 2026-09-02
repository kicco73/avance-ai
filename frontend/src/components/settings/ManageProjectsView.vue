<script setup>
// Settings > Manage projects: one row per project with its three-state
// status (see backend ProjectService._project_status) and revision info.
// The status dot toggles running <-> manually_paused only; 'paused' needs an external fix.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getProjectFiles, getProjectMetadata, getProjectsRuntimeStatus, projectFileContentUrl, putProjectPause, putProjectResume } from '../../api.js'
import { confirmDialog, customDialog } from '../../dialogStore.js'
import { setCanvasColor, restoreCanvasColor } from '../../canvasColor.js'
import SettingsMenu from './SettingsMenu.vue'
import StatusToggleButton from './StatusToggleButton.vue'
import ProfileMenu from '../ProfileMenu.vue'
import AppHeader from '../AppHeader.vue'
import ShareProjectDialog from './ShareProjectDialog.vue'
import avanceLogoUrl from '../../assets/avance-logo.png'
import avanceLogoLargeUrl from '../../assets/avance-logo-large.png'

const props = defineProps({
  uploading: { type: Boolean, default: false },
  // 0-100, or null before the first progress chunk has arrived — see
  // SessionsTree.vue's identical importProgress for the same reasoning.
  uploadProgress: { type: Number, default: null },
  uploadProjectName: { type: String, default: null },
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
  'new-project', 'upload', 'delete', 'edit', 'label', 'download', 'chat',
  'manage-users', 'manage-services', 'about',
  'profile', 'logout'
])

const envTag = (() => {
  const host = window.location.hostname
  if (host === 'localhost') return { label: 'DEV', className: 'manage-projects-env-tag-dev' }
  if (host.startsWith('staging')) return { label: 'STAGING', className: 'manage-projects-env-tag-staging' }
  return { label: 'PROD', className: 'manage-projects-env-tag-prod' }
})()

const rows = ref([])
const loading = ref(true)
// Name of the project with a pause/resume request in flight; disables
// only that row's button.
const togglingProject = ref(null)
// name -> { id, ui_label, ui_description }, filled in after the runtime-status
// list loads — the project detail card's own content (title/id/description).
const metadataByName = ref({})
// name -> its resolved 'aspect/icon.<ext>' archive name (whatever image
// extension it was uploaded with — see findIconFile), filled in alongside
// metadataByName. Absent (not just falsy) for a project with no icon file.
const iconFileByName = ref({})
// name -> true once that project's icon request has failed (e.g. deleted
// after iconFileByName was resolved), same fallback idiom as ProfileMenu.vue's
// avatar image.
const iconFailedByName = ref({})

// Matches whatever extension a project's icon was actually uploaded
// with — the backend accepts any of these under aspect/ (see
// backend/src/project/archive/layout.py's IMAGE_CONTENT_TYPE_BY_EXTENSION),
// so the icon file itself is never assumed to be .png.
const ICON_FILE_RE = /^aspect\/icon\.(png|jpe?g|gif|webp|svg)$/i
function findIconFile(files) {
  return files.find((name) => ICON_FILE_RE.test(name)) ?? null
}

const bodyEl = ref(null)
const bodyWidth = ref(0)
const actionsBlockWidth = ref(0)
const openMenuFor = ref(null)
const addMenuOpen = ref(false)
let bodyResizeObserver = null

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

const actionsOverflow = computed(() => actionsBlockWidth.value > 0 && bodyWidth.value > 0 && bodyWidth.value < actionsBlockWidth.value)

function captureActionsWidth(rowEl) {
  if (actionsBlockWidth.value > 0 || !rowEl) return
  const nameCell = rowEl.querySelector('.manage-projects-name')
  const statusBtn = rowEl.querySelector('.status-toggle-btn')
  const actionsRow = rowEl.querySelector('.manage-projects-actions-row')
  if (!nameCell || !statusBtn || !actionsRow) return
  actionsBlockWidth.value = nameCell.getBoundingClientRect().width + statusBtn.getBoundingClientRect().width + actionsRow.getBoundingClientRect().width
}

function setFirstRowEl(el) {
  if (!el) return
  nextTick(() => captureActionsWidth(el))
}

function toggleActionsMenu(name) {
  openMenuFor.value = openMenuFor.value === name ? null : name
}

function toggleAddMenu() {
  addMenuOpen.value = !addMenuOpen.value
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
  if (openMenuFor.value && !event.target.closest('.manage-projects-actions-menu')) {
    openMenuFor.value = null
  }
  if (addMenuOpen.value && !event.target.closest('.manage-projects-add-menu')) {
    addMenuOpen.value = false
  }
}

async function loadMetadata(names) {
  const results = await Promise.allSettled(names.map((name) => getProjectMetadata(name)))
  results.forEach((result, i) => {
    if (result.status === 'fulfilled') metadataByName.value[names[i]] = result.value.project
  })
}

async function loadIcons(names) {
  const results = await Promise.allSettled(names.map((name) => getProjectFiles(name)))
  results.forEach((result, i) => {
    if (result.status !== 'fulfilled') return
    const iconFile = findIconFile(result.value.files)
    if (iconFile) iconFileByName.value[names[i]] = iconFile
  })
}

async function load() {
  loading.value = true
  try {
    const res = await getProjectsRuntimeStatus()
    rows.value = res.projects
    loadMetadata(rows.value.map((row) => row.name))
    loadIcons(rows.value.map((row) => row.name))
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
  if (!ready || !props.uploadProjectName) return
  let iconFile = null
  try {
    const { files } = await getProjectFiles(props.uploadProjectName)
    iconFile = findIconFile(files)
  } catch {
    return // already surfaced via apiFetch
  }
  if (!iconFile) return // no icon in this upload — the fallback logo stays put
  uploadIconFile.value = iconFile
  const preload = new Image()
  preload.onload = () => { uploadIconLoaded.value = true }
  preload.onerror = () => { uploadIconFailed.value = true }
  preload.src = projectFileContentUrl(props.uploadProjectName, iconFile)
})

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

function selectShare(name) {
  customDialog({ component: ShareProjectDialog, props: { projectName: name, uiLabel: projectTitle(name) } })
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
  previousCanvasColor = setCanvasColor('#ffffff')
  bodyResizeObserver = new ResizeObserver((entries) => {
    bodyWidth.value = entries[0].contentRect.width
  })
  bodyResizeObserver.observe(bodyEl.value)
  headerResizeObserver = new ResizeObserver(handleHeaderResize)
  headerResizeObserver.observe(headerEl.value.el)
  headerResizeObserver.observe(headerLeftEl.value)
  headerResizeObserver.observe(headerActionsEl.value)
  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  restoreCanvasColor(previousCanvasColor)
  bodyResizeObserver?.disconnect()
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
                      <span>Upload project...</span>
                    </button>
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
          <ProfileMenu :profile="profile" @profile="emit('profile')" @logout="emit('logout')" />
        </div>
      </template>
    </AppHeader>

    <div class="manage-projects-body" ref="bodyEl">
      <p v-if="loading" class="manage-projects-status">Loading…</p>
      <p v-else-if="!rows.length && !uploading" class="manage-projects-status">No projects yet.</p>

      <table v-else class="manage-projects-table">
        <tbody>
          <tr v-for="(row, index) in rows" :key="row.name" :ref="index === 0 ? setFirstRowEl : undefined">
            <td class="manage-projects-name">
              <button type="button" class="project-card" title="Open chat" @click="selectChat(row.name)">
                <img
                  v-if="iconFileByName[row.name] && !iconFailedByName[row.name]"
                  :src="projectFileContentUrl(row.name, iconFileByName[row.name])"
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
            <td class="manage-projects-col-spacer"></td>
            <td class="manage-projects-col-status-actions" :class="{ 'manage-projects-col-status-actions-collapsed': actionsOverflow }">
              <div class="manage-projects-status-actions-row">
                <StatusToggleButton
                  :status="row.status"
                  :disabled="row.status === 'paused' || togglingProject === row.name"
                  :title="statusTitle(row)"
                  @click="toggleStatus(row)"
                />
                <div v-if="!actionsOverflow" class="manage-projects-actions-row">
                  <button type="button" class="manage-projects-edit-btn" title="Edit project" @click="selectEdit(row.name)">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                      <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34a.996.996 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" />
                    </svg>
                  </button>
                  <button type="button" class="manage-projects-label-btn" title="Label sessions" @click="selectLabelSessions(row.name)">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                      <path d="M21.41 11.58l-9-9C12.05 2.22 11.55 2 11 2H4c-1.1 0-2 .9-2 2v7c0 .55.22 1.05.59 1.41l9 9c.36.36.86.59 1.41.59s1.05-.23 1.41-.59l7-7c.37-.36.59-.86.59-1.41s-.23-1.06-.59-1.42zM6.5 8C5.67 8 5 7.33 5 6.5S5.67 5 6.5 5 8 5.67 8 6.5 7.33 8 6.5 8z" />
                    </svg>
                  </button>
                  <button type="button" class="manage-projects-download-btn" title="Download project" @click="selectDownload(row.name)">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                      <path d="M12 3a1 1 0 0 1 1 1v9.59l2.3-2.3a1 1 0 1 1 1.4 1.42l-4 4a1 1 0 0 1-1.4 0l-4-4a1 1 0 1 1 1.4-1.42l2.3 2.3V4a1 1 0 0 1 1-1zM5 19a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H6a1 1 0 0 1-1-1z" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    class="manage-projects-share-btn"
                    title="Share project"
                    @click="selectShare(row.name)"
                  >
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                      <path d="M3 11h8V3H3v8zm2-6h4v4H5V5zM3 21h8v-8H3v8zm2-6h4v4H5v-4zM13 3v8h8V3h-8zm6 6h-4V5h4v4zM19 19h2v2h-2zM13 13h2v2h-2zM15 15h2v2h-2zM13 17h2v2h-2zM15 19h2v2h-2zM17 17h2v2h-2zM17 13h2v2h-2zM19 15h2v2h-2z" />
                    </svg>
                  </button>
                  <button type="button" class="manage-projects-delete-btn" title="Delete project" @click="selectDelete(row.name)">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                      <path d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" />
                    </svg>
                  </button>
                </div>
                <div v-else class="manage-projects-actions-menu">
                  <button type="button" class="manage-projects-menu-btn" title="More actions" @click="toggleActionsMenu(row.name)">⋮</button>
                  <Transition name="manage-projects-menu-panel">
                    <div v-if="openMenuFor === row.name" class="manage-projects-menu-panel">
                      <ul class="manage-projects-menu-list">
                        <li>
                          <button type="button" class="manage-projects-menu-item" @click="selectEdit(row.name); openMenuFor = null">
                            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                              <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34a.996.996 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" />
                            </svg>
                            <span>Edit project</span>
                          </button>
                        </li>
                        <li>
                          <button type="button" class="manage-projects-menu-item" @click="selectLabelSessions(row.name); openMenuFor = null">
                            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                              <path d="M21.41 11.58l-9-9C12.05 2.22 11.55 2 11 2H4c-1.1 0-2 .9-2 2v7c0 .55.22 1.05.59 1.41l9 9c.36.36.86.59 1.41.59s1.05-.23 1.41-.59l7-7c.37-.36.59-.86.59-1.41s-.23-1.06-.59-1.42zM6.5 8C5.67 8 5 7.33 5 6.5S5.67 5 6.5 5 8 5.67 8 6.5 7.33 8 6.5 8z" />
                            </svg>
                            <span>Label sessions</span>
                          </button>
                        </li>
                        <li>
                          <button type="button" class="manage-projects-menu-item" @click="selectDownload(row.name); openMenuFor = null">
                            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                              <path d="M12 3a1 1 0 0 1 1 1v9.59l2.3-2.3a1 1 0 1 1 1.4 1.42l-4 4a1 1 0 0 1-1.4 0l-4-4a1 1 0 1 1 1.4-1.42l2.3 2.3V4a1 1 0 0 1 1-1zM5 19a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H6a1 1 0 0 1-1-1z" />
                            </svg>
                            <span>Download project</span>
                          </button>
                        </li>
                        <li>
                          <button
                            type="button"
                            class="manage-projects-menu-item"
                            @click="selectShare(row.name); openMenuFor = null"
                          >
                            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                              <path d="M3 11h8V3H3v8zm2-6h4v4H5V5zM3 21h8v-8H3v8zm2-6h4v4H5v-4zM13 3v8h8V3h-8zm6 6h-4V5h4v4zM19 19h2v2h-2zM13 13h2v2h-2zM15 15h2v2h-2zM13 17h2v2h-2zM15 19h2v2h-2zM17 17h2v2h-2zM17 13h2v2h-2zM19 15h2v2h-2z" />
                            </svg>
                            <span>Share project</span>
                          </button>
                        </li>
                        <li>
                          <button type="button" class="manage-projects-menu-item manage-projects-menu-item-danger" @click="selectDelete(row.name); openMenuFor = null">
                            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
                              <path d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" />
                            </svg>
                            <span>Delete project</span>
                          </button>
                        </li>
                      </ul>
                    </div>
                  </Transition>
                </div>
              </div>
            </td>
          </tr>
          <tr v-if="uploading">
            <td class="manage-projects-name">
              <div class="project-card project-card-placeholder">
                <span class="project-card-icon-wrap">
                  <img :src="avanceLogoUrl" class="project-card-fallback-logo manage-projects-upload-icon-glow" alt="" />
                  <Transition name="manage-projects-upload-icon">
                    <img
                      v-if="uploadIconLoaded && !uploadIconFailed"
                      :src="projectFileContentUrl(uploadProjectName, uploadIconFile)"
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
            <td class="manage-projects-col-spacer"></td>
            <td class="manage-projects-col-status-actions"></td>
          </tr>
        </tbody>
      </table>
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

.manage-projects-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-top: 20px;
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

.manage-projects-status {
  margin: 0;
  padding: 0.75rem 0 0.75rem 20px;
  font-size: 0.9rem;
  color: #666;
}

.manage-projects-table {
  width: 100%;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 0.88rem;
}

.manage-projects-name {
  width: 320px;
}

.manage-projects-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #eee;
  vertical-align: middle;
}

.manage-projects-col-status-actions {
  width: 256px;
}

.manage-projects-col-status-actions-collapsed {
  width: 96px;
}

.manage-projects-status-actions-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
  width: 100%;
}

.manage-projects-actions-row {
  display: flex;
  justify-content: flex-end;
}

.project-card {
  box-sizing: border-box;
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
  flex: 1;
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

.manage-projects-edit-btn {
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

.manage-projects-edit-btn:hover {
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

.manage-projects-share-btn {
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

.manage-projects-share-btn:hover {
  background: #f0f4fa;
}

.manage-projects-delete-btn {
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

.manage-projects-delete-btn:hover {
  background: #fdecea;
}

.manage-projects-actions-menu {
  position: relative;
  display: flex;
  justify-content: flex-end;
}

.manage-projects-menu-btn {
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
  font-size: 1.3rem;
  line-height: 1;
}

.manage-projects-menu-btn:hover {
  background: #f0f4fa;
}

.manage-projects-menu-panel {
  position: absolute;
  top: calc(100% + 0.2rem);
  right: 0;
  min-width: 12rem;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 100;
  overflow: hidden;
  transform-origin: top right;
}

.manage-projects-menu-panel-enter-active,
.manage-projects-menu-panel-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.manage-projects-menu-panel-enter-from,
.manage-projects-menu-panel-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.96);
}

.manage-projects-menu-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
}

.manage-projects-menu-item {
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

.manage-projects-menu-item:hover {
  background: #f0f4fa;
}

.manage-projects-menu-item svg {
  flex-shrink: 0;
  color: #4a6fa5;
}

.manage-projects-menu-item-danger {
  color: #c62828;
}

.manage-projects-menu-item-danger svg {
  color: #c62828;
}

.manage-projects-menu-item-danger:hover {
  background: #fdecea;
}
</style>
