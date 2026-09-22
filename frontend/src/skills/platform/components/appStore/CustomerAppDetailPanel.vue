<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { deleteInstallApp, driveFileContentUrl, getDriveFiles, getProjectSignals, getUserLatestSignals } from '../../api.js'
import { getAppSessionSummaries } from '../../api/appStore.js'
import { busChannel } from '../../../../busChannel.js'
import { confirmDialog } from '../../../../dialogStore.js'
import { renderMarkdown } from '../../../../markdown.js'
import { openMediaDialog } from '../../../../openMediaDialog.js'
import InspectorSignalList from '../inspector/InspectorSignalList.vue'
import TimelineChart from '../settings/TimelineChart.vue'
import { valuesToSignalValues } from '../../../../testTimeline.js'

const props = defineProps({
  app: { type: Object, required: true },
  profile: { type: Object, default: null }
})

const emit = defineEmits(['open'])

const uninstalling = ref(false)
const uninstallMenuOpen = ref(false)
const uninstallMenuRootEl = ref(null)
const sessionSummaries = ref([])

const tabs = [{ id: 'summary', label: 'Summary' }, { id: 'docs', label: 'Drive' }, { id: 'signals', label: 'Signals' }]
const activeTab = ref('summary')

const driveFiles = ref([])
const driveFilesLoading = ref(false)
let driveFilesLoaded = false

const signalsUsername = computed(() => props.profile?.email ?? props.profile?.id ?? null)
const signalColorMap = ref(null)
const latestSignals = ref({ last_session: null, session_id: null, values: null })
const latestSignalsLoading = ref(false)
const latestSignalValues = computed(() => valuesToSignalValues(latestSignals.value.values))
const signalDefs = ref([])
const relevantSignals = computed(() => signalDefs.value.filter((s) => s.relevant))
let latestSignalsLoaded = false

async function loadLatestSignals() {
  if (!signalsUsername.value) return
  latestSignalsLoading.value = true
  try {
    latestSignals.value = await getUserLatestSignals(props.app.id, signalsUsername.value)
    signalDefs.value = (await getProjectSignals(props.app.id, null, latestSignals.value.session_id)).signals
  } catch {
    latestSignals.value = { last_session: null, session_id: null, values: null }
    signalDefs.value = []
  } finally {
    latestSignalsLoading.value = false
  }
}

async function loadDriveFiles() {
  driveFilesLoading.value = true
  try {
    driveFiles.value = (await getDriveFiles(props.app.id)).files
  } catch {
    driveFiles.value = []
  } finally {
    driveFilesLoading.value = false
  }
}

function openDriveFile(file) {
  openMediaDialog(driveFileContentUrl(props.app.id, file.path))
}

function formatFileSize(bytes) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`
}

watch(activeTab, (tab) => {
  if (tab === 'signals' && !latestSignalsLoaded) {
    latestSignalsLoaded = true
    loadLatestSignals()
  }
  if (tab === 'docs' && !driveFilesLoaded) {
    driveFilesLoaded = true
    loadDriveFiles()
  }
})

function formatClosedAt(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

onMounted(async () => {
  try {
    sessionSummaries.value = (await getAppSessionSummaries(props.app.id)).sessions
  } catch {
  }
})

let unsubscribeDrive = null

onMounted(() => {
  unsubscribeDrive = busChannel.subscribe('output.drive', (frame) => {
    if (frame.project_id !== props.app.id) return
    driveFilesLoaded = true
    loadDriveFiles()
  })
})

onBeforeUnmount(() => unsubscribeDrive?.())

function appTitle(app) {
  return app?.ui_label || app?.id || ''
}

function toggleUninstallMenu() {
  uninstallMenuOpen.value = !uninstallMenuOpen.value
}

function handleUninstallMenuDocumentClick(event) {
  if (uninstallMenuOpen.value && uninstallMenuRootEl.value && !uninstallMenuRootEl.value.contains(event.target)) {
    uninstallMenuOpen.value = false
  }
}

document.addEventListener('click', handleUninstallMenuDocumentClick, true)

onBeforeUnmount(() => document.removeEventListener('click', handleUninstallMenuDocumentClick, true))

async function selectUninstallFromMenu() {
  uninstallMenuOpen.value = false
  const app = props.app
  const ok = await confirmDialog({
    title: 'Uninstall',
    body: `Uninstall "${appTitle(app)}"? You'll also permanently lose all data recorded for it.`,
    okLabel: 'Uninstall',
    danger: true
  })
  if (!ok) return
  uninstalling.value = true
  try {
    await deleteInstallApp(app.id)
    app.installed = false
  } catch {
  } finally {
    uninstalling.value = false
  }
}
</script>

<template>
  <div class="customer-app-detail-tabbar">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      type="button"
      class="customer-app-detail-tab"
      :class="{ 'customer-app-detail-tab-active': activeTab === tab.id }"
      @click="activeTab = tab.id"
    >{{ tab.label }}</button>
  </div>

  <template v-if="activeTab === 'summary'">
    <div class="customer-app-detail-header-row">
      <div class="customer-app-detail-menu" ref="uninstallMenuRootEl">
        <button type="button" class="customer-app-detail-menu-btn" title="More actions" @click="toggleUninstallMenu">⋮</button>
        <Transition name="customer-app-detail-menu-panel">
          <ul v-if="uninstallMenuOpen" class="customer-app-detail-menu-list">
            <li>
              <button type="button" class="customer-app-detail-menu-item" :disabled="uninstalling" @click="selectUninstallFromMenu">Uninstall</button>
            </li>
          </ul>
        </Transition>
      </div>
    </div>

    <div v-if="app.ai_summary" class="customer-app-detail-ai-summary" v-html="renderMarkdown(app.ai_summary)"></div>

    <button type="button" class="customer-app-detail-chat-now-btn" @click="emit('open', app.id)">Open</button>

    <hr v-if="sessionSummaries.length" class="customer-app-detail-divider" />
    <h3 v-if="sessionSummaries.length" class="customer-app-detail-subtitle">Last sessions</h3>
    <div v-if="sessionSummaries.length" class="customer-app-detail-session-summaries">
      <div v-for="session in sessionSummaries" :key="session.id" class="customer-app-detail-session-summary">
        <div class="customer-app-detail-session-summary-header">
          <span class="customer-app-detail-session-summary-title">{{ session.title }}</span>
          <span class="customer-app-detail-session-summary-date">{{ formatClosedAt(session.closed_at) }}</span>
        </div>
        <div class="customer-app-detail-session-summary-text" v-html="renderMarkdown(session.ai_summary)"></div>
      </div>
    </div>
  </template>

  <div v-else-if="activeTab === 'signals'" class="customer-app-detail-signals-tab">
    <p v-if="!signalsUsername" class="customer-app-detail-status">Your profile has no email on file.</p>
    <template v-else>
      <TimelineChart :project-id="app.id" :username="signalsUsername" @colors="signalColorMap = $event" />
      <p v-if="latestSignalsLoading" class="customer-app-detail-status">Loading…</p>
      <p v-else-if="!latestSignals.last_session" class="customer-app-detail-status">
        You have no live sessions in this app yet.
      </p>
      <p v-else-if="!relevantSignals.length" class="customer-app-detail-status">No signals computed yet.</p>
      <InspectorSignalList
        v-else
        :signals="relevantSignals"
        :signal-values="latestSignalValues"
        :signal-colors="signalColorMap"
      />
    </template>
  </div>

  <div v-else-if="activeTab === 'docs'" class="customer-app-detail-docs-tab">
    <p v-if="driveFilesLoading" class="customer-app-detail-status">Loading…</p>
    <p v-else-if="!driveFiles.length" class="customer-app-detail-status">Nothing has been saved here yet.</p>
    <ul v-else class="customer-app-detail-docs-list">
      <li v-for="file in driveFiles" :key="file.path">
        <button type="button" class="customer-app-detail-docs-item" @click="openDriveFile(file)">
          <span class="customer-app-detail-docs-path">{{ file.path }}</span>
          <span class="customer-app-detail-docs-meta">{{ formatFileSize(file.size) }} · {{ formatClosedAt(file.updated_at) }}</span>
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.customer-app-detail-tabbar {
  flex-shrink: 0;
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid #ddd;
}

.customer-app-detail-tab {
  padding: 0.45rem 0.9rem;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: none;
  cursor: pointer;
  font-size: 0.82rem;
  color: #666;
}

.customer-app-detail-tab:hover {
  color: #333;
}

.customer-app-detail-tab-active {
  color: #2c4d7a;
  font-weight: 600;
  border-bottom-color: #4a6fa5;
}

.customer-app-detail-signals-tab {
  flex: 1;
  min-height: 300px;
  display: flex;
  flex-direction: column;
}

.customer-app-detail-status {
  margin: 0;
  padding: 0.75rem 0;
  font-size: 0.9rem;
  color: #666;
}

.customer-app-detail-header-row {
  flex-shrink: 0;
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  gap: 0.5rem;
}

.customer-app-detail-divider {
  width: 100%;
  margin: 0.6rem 0 0;
  border: none;
  border-top: 1px solid #eee;
}

.customer-app-detail-subtitle {
  margin: 0.4rem 0 0;
  font-size: 0.9rem;
  color: #555;
}

.customer-app-detail-menu {
  position: relative;
  flex-shrink: 0;
}

.customer-app-detail-menu-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.8rem;
  height: 1.8rem;
  border-radius: 6px;
  border: 1px solid #ddd;
  background: white;
  color: #555;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}

.customer-app-detail-menu-btn:hover {
  background: #f0f0f0;
}

.customer-app-detail-menu-list {
  position: absolute;
  top: calc(100% + 0.3rem);
  right: 0;
  min-width: 140px;
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 10;
}

.customer-app-detail-menu-item {
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  color: #c62828;
}

.customer-app-detail-menu-item:hover {
  background: #fbeaea;
}

.customer-app-detail-menu-panel-enter-active,
.customer-app-detail-menu-panel-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.customer-app-detail-menu-panel-enter-from,
.customer-app-detail-menu-panel-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.96);
}

.customer-app-detail-chat-now-btn {
  flex-shrink: 0;
  align-self: flex-start;
  padding: 0.45rem 1.1rem;
  border-radius: 6px;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid #4a6fa5;
  background: #4a6fa5;
  color: white;
}

.customer-app-detail-chat-now-btn:hover {
  background: #3d5c8a;
}

.customer-app-detail-ai-summary {
  color: #555;
  font-size: 0.9rem;
  line-height: 1.5;
  overflow-y: auto;
}

.customer-app-detail-session-summaries {
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
  overflow-y: auto;
}

.customer-app-detail-session-summary {
  padding: 0.6rem 0.8rem;
  border: 1px solid #eee;
  border-radius: 8px;
  background: #fafafa;
}

.customer-app-detail-session-summary-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.6rem;
  margin-bottom: 0.25rem;
}

.customer-app-detail-session-summary-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #333;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.customer-app-detail-session-summary-date {
  flex-shrink: 0;
  color: #999;
  font-size: 0.72rem;
}

.customer-app-detail-session-summary-text {
  color: #555;
  font-size: 0.85rem;
  line-height: 1.5;
}

.customer-app-detail-docs-tab {
  flex: 1;
  min-height: 300px;
  overflow-y: auto;
}

.customer-app-detail-docs-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.customer-app-detail-docs-item {
  width: 100%;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.6rem;
  padding: 0.55rem 0.7rem;
  border: 1px solid #eee;
  border-radius: 8px;
  background: #fafafa;
  cursor: pointer;
  text-align: left;
}

.customer-app-detail-docs-item:hover {
  background: #f0f0f0;
}

.customer-app-detail-docs-path {
  font-size: 0.85rem;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.customer-app-detail-docs-meta {
  flex-shrink: 0;
  font-size: 0.72rem;
  color: #999;
}
</style>
