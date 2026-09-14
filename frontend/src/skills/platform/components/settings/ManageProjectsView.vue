<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getProjectFiles, getProjectMetadata, getProjectsRuntimeStatus, projectFileContentUrl, putProjectPause, putProjectResume } from '../../api.js'
import { confirmDialog, customDialog } from '../../../../dialogStore.js'
import { setCanvasColor, restoreCanvasColor } from '../../../../canvasColor.js'
import { findIconFile } from '../../../../projectIcon.js'
import { ensureProjectFileTypes } from '../../../../projectFileTypes.js'
import { useHeaderLogoFit } from '../../../../composables/useHeaderLogoFit.js'
import { onProjectChanged, onProjectsChanged } from '../../../../projectChangeEvents.js'
import { familySections } from '../../familySections.js'
import SettingsMenu from './SettingsMenu.vue'
import StatusToggleButton from '../../../../components/services/StatusToggleButton.vue'
import ProfileMenu from '../../../../components/ProfileMenu.vue'
import AppHeader from '../../../../components/AppHeader.vue'
import ProjectDetailPanel from './ProjectDetailPanel.vue'
import ShareProjectDialog from './ShareProjectDialog.vue'
import AddProjectMenu from './AddProjectMenu.vue'
import ProjectRowCard from './ProjectRowCard.vue'
import UploadPlaceholderCard from './UploadPlaceholderCard.vue'
import avanceLogoLargeUrl from '../../../../assets/avance-logo-large.png'

const props = defineProps({
  uploading: { type: Boolean, default: false },
  uploadProgress: { type: Number, default: null },
  uploadProjectId: { type: String, default: null },
  uploadIconReady: { type: Boolean, default: false },
  role: { type: String, default: null },
  profile: { type: Object, default: null }
})

const emit = defineEmits([
  'new-project', 'upload', 'delete', 'edit', 'label', 'download', 'publish', 'open-skill-view',
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
const togglingProject = ref(null)
const metadataById = ref({})
const iconFileById = ref({})
const iconFailedById = ref({})

const selectedProjectId = ref(null)
const selectedRow = computed(() => rows.value.find((row) => row.id === selectedProjectId.value) ?? null)

const selectedPanelProps = computed(() => {
  const row = selectedRow.value
  if (!row) return null
  return {
    projectId: row.id,
    title: projectTitle(row.id),
    description: projectDescription(row.id),
    broken: row.broken ?? null,
    publishedRevision: row.published_revision ?? null,
    revision: row.revision ?? null,
  }
})

function selectProject(id) {
  selectedProjectId.value = id
}

const headerEl = ref(null)
const headerLeftEl = ref(null)
const headerActionsEl = ref(null)
const { logoFits, setLogoBtnEl } = useHeaderLogoFit(headerEl, headerLeftEl, headerActionsEl)

async function loadMetadata(ids) {
  const results = await Promise.allSettled(ids.map((id) => getProjectMetadata(id)))
  results.forEach((result, i) => {
    if (result.status === 'fulfilled') metadataById.value[ids[i]] = result.value.project
  })
}

async function loadIcons(ids) {
  await ensureProjectFileTypes()
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
  } catch {
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

function iconSrcFor(id) {
  if (!iconFileById.value[id] || iconFailedById.value[id]) return null
  return projectFileContentUrl(id, iconFileById.value[id])
}

const sections = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  const matching = !q ? rows.value : rows.value.filter((row) => {
    const label = projectTitle(row.id).toLowerCase()
    const description = (projectDescription(row.id) || '').toLowerCase()
    return label.includes(q) || description.includes(q)
  })
  return familySections(matching.map((row) => ({
    family: metadataById.value[row.id]?.family,
    title: projectTitle(row.id),
    item: row,
  })))
})

const visibleRows = computed(() => sections.value.flatMap((section) => section.items.map((entry) => entry.item)))

function replaceRow(updated) {
  const idx = rows.value.findIndex((r) => r.id === updated.id)
  if (idx !== -1) rows.value[idx] = updated
}

async function toggleStatus(row) {
  if (row.status === 'paused') return
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
  } finally {
    togglingProject.value = null
  }
}

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

function selectPublish(id) {
  emit('publish', id)
}

function selectSkillView(view, id) {
  emit('open-skill-view', view, id)
}

function statusTitle(row) {
  if (row.status === 'running') return 'Running'
  if (row.status === 'manually_paused') return 'Manually paused'
  return row.paused_reason ?? 'Paused'
}

let previousCanvasColor = ''

onMounted(() => {
  load()
  previousCanvasColor = setCanvasColor('#ffffff')
})

onBeforeUnmount(onProjectsChanged(load))
onBeforeUnmount(onProjectChanged(load))

onBeforeUnmount(() => {
  restoreCanvasColor(previousCanvasColor)
})

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
          <AddProjectMenu @new-project="emit('new-project')" @upload="emit('upload')" />
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
            <colgroup>
              <col class="manage-projects-col-name" />
              <col class="manage-projects-col-status" />
            </colgroup>
            <tbody v-for="section in sections" :key="section.key">
              <tr>
                <th colspan="2" class="manage-projects-section-header">{{ section.label }}</th>
              </tr>
              <tr
                v-for="{ item: row } in section.items"
                :key="row.id"
                class="manage-projects-row"
                :class="{ 'manage-projects-row-selected': selectedProjectId === row.id }"
                @click="selectProject(row.id)"
                @dblclick="selectEdit(row.id)"
              >
                <td class="manage-projects-name">
                  <ProjectRowCard
                    :row="row"
                    :title="projectTitle(row.id)"
                    :description="projectDescription(row.id)"
                    :icon-src="iconSrcFor(row.id)"
                    @icon-error="iconFailedById[row.id] = true"
                  />
                </td>
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
              </tr>
            </tbody>
            <tbody>
              <tr v-if="uploading">
                <td class="manage-projects-name">
                  <UploadPlaceholderCard
                    :upload-progress="uploadProgress"
                    :upload-project-id="uploadProjectId"
                    :upload-icon-ready="uploadIconReady"
                  />
                </td>
                <td class="manage-projects-col-status-actions"></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="manage-projects-preview">
        <div v-if="selectedPanelProps" :key="selectedRow.id" class="manage-projects-detail">
          <ProjectDetailPanel
            v-bind="selectedPanelProps"
            @edit="selectEdit"
            @label="selectLabelSessions"
            @download="selectDownload"
            @share="selectShare"
            @delete="selectDelete"
            @publish="selectPublish"
            @open-skill-view="selectSkillView"
          />
        </div>
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
  background: #f2f2f7;
  border-radius: 10px;
  padding-bottom: calc(0.75rem + var(--safe-area-bottom));
}

.manage-projects-row {
  cursor: pointer;
}

.manage-projects-row .project-card {
  position: relative;
  max-width: none;
  border: none;
  border-radius: 0;
  background: white;
}

.manage-projects-row + .manage-projects-row .project-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: calc(0.7rem + 65px + 0.6rem);
  right: 0;
  height: 1px;
  background: #e5e5ea;
}

.manage-projects-table tbody tr:nth-of-type(2) .project-card {
  border-radius: 10px 10px 0 0;
}

.manage-projects-table tbody tr:last-of-type .project-card {
  border-radius: 0 0 10px 10px;
}

.manage-projects-table tbody tr:nth-of-type(2):last-of-type .project-card {
  border-radius: 10px;
}

.manage-projects-row-selected .project-card {
  background: #eef3fa;
}

.manage-projects-preview {
  position: relative;
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  overflow-y: auto;
  padding: 20px 0 var(--safe-area-bottom);
}

.manage-projects-detail {
  flex: 1 0 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.manage-projects-status {
  margin: 0;
  padding: 0.75rem 0 0.75rem 20px;
  font-size: 0.9rem;
  color: #666;
}

.manage-projects-table {
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 0.88rem;
}

.manage-projects-col-status {
  width: 4.38rem;
}

.manage-projects-col-name {
  width: 320px;
}

.manage-projects-table td.manage-projects-name {
  width: 320px;
  padding: 0 0 0 0.75rem;
}

.manage-projects-table td {
  padding: 0 0.75rem;
  vertical-align: middle;
}

.manage-projects-section-header {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 0.75rem 0.75rem 0.35rem 15px;
  background: #f2f2f7;
  color: #8e8e93;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  text-align: left;
}

.manage-projects-status-actions-row {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 2px;
  width: 100%;
}
</style>
