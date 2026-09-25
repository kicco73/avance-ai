<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { deleteDriveFile, driveFileContentUrl, getDriveFiles, getProjectSignals, getUserLatestSignals } from '../../api.js'
import { getAppSessionSummaries } from '../../api/appStore.js'
import { busChannel } from '../../../../busChannel.js'
import { confirmDialog } from '../../../../dialogStore.js'
import { renderMarkdown } from '../../../../markdown.js'
import { openMediaDialog } from '../../../../openMediaDialog.js'
import InspectorSignalList from '../inspector/InspectorSignalList.vue'
import TimelineChart from '../settings/TimelineChart.vue'
import AppIdentityHeader from './AppIdentityHeader.vue'
import { valuesToSignalValues } from '../../../../testTimeline.js'

const props = defineProps({
  app: { type: Object, required: true },
  profile: { type: Object, default: null }
})

const emit = defineEmits(['open'])

const sessionSummaries = ref([])
const sessionSummariesLoading = ref(true)

const tabs = [{ id: 'summary', label: 'Summary', ai: true }, { id: 'signals', label: 'Signals', ai: true }, { id: 'docs', label: 'Drive' }]
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

const deletingPath = ref(null)

async function deleteFile(file) {
  const ok = await confirmDialog({
    title: 'Delete file',
    body: `Delete "${file.path}" from your drive? This cannot be undone.`,
    okLabel: 'Delete',
    danger: true
  })
  if (!ok) return
  deletingPath.value = file.path
  try {
    await deleteDriveFile(props.app.id, file.path)
  } catch {
  } finally {
    deletingPath.value = null
  }
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
  } finally {
    sessionSummariesLoading.value = false
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
</script>

<template>
  <AppIdentityHeader :app="app">
    <button type="button" class="customer-app-detail-chat-now-btn" @click="emit('open', app.id)">Open</button>
  </AppIdentityHeader>

  <div class="customer-app-detail-tabbar">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      type="button"
      class="customer-app-detail-tab"
      :class="{ 'customer-app-detail-tab-active': activeTab === tab.id }"
      @click="activeTab = tab.id"
    >
      <span v-if="tab.ai" class="customer-app-detail-tab-ai-icon" title="Produced by the AI">
        <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
      </span>{{ tab.label }}
    </button>
  </div>

  <template v-if="activeTab === 'summary'">
    <div v-if="app.ai_summary" class="customer-app-detail-ai-summary" v-html="renderMarkdown(app.ai_summary)"></div>
    <p v-else class="customer-app-detail-status">
      No summary yet. This is where you'll find a summary of your activity across all your sessions in this app, updated as you use it.
    </p>

    <hr class="customer-app-detail-divider" />
    <h3 class="customer-app-detail-subtitle">Last sessions</h3>
    <p v-if="sessionSummariesLoading" class="customer-app-detail-status">Loading…</p>
    <p v-else-if="!sessionSummaries.length" class="customer-app-detail-status">
      No sessions yet. Once you start using the app, a summary of each session's activity will appear here.
    </p>
    <div v-else class="customer-app-detail-session-summaries">
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
    <p v-if="latestSignalsLoading" class="customer-app-detail-status">Loading…</p>
    <p v-else-if="!relevantSignals.length" class="customer-app-detail-status">This app doesn't use signals.</p>
    <template v-else>
      <TimelineChart :project-id="app.id" :username="signalsUsername" @colors="signalColorMap = $event" />
      <InspectorSignalList
        :signals="relevantSignals"
        :signal-values="latestSignalValues"
        :signal-colors="signalColorMap"
      />
    </template>
  </div>

  <div v-else-if="activeTab === 'docs'" class="customer-app-detail-docs-tab">
    <p v-if="driveFilesLoading" class="customer-app-detail-status">Loading…</p>
    <p v-else-if="!driveFiles.length" class="customer-app-detail-status">
      Whenever the app saves a file for you, it will appear in this section, always available to download.
    </p>
    <ul v-else class="customer-app-detail-docs-list">
      <li v-for="file in driveFiles" :key="file.path" class="customer-app-detail-docs-row">
        <button type="button" class="customer-app-detail-docs-item" @click="openDriveFile(file)">
          <span class="customer-app-detail-docs-path">{{ file.path }}</span>
          <span class="customer-app-detail-docs-meta">{{ formatFileSize(file.size) }} · {{ formatClosedAt(file.updated_at) }}</span>
        </button>
        <button
          type="button"
          class="customer-app-detail-docs-delete"
          title="Delete this file"
          :disabled="deletingPath === file.path"
          @click="deleteFile(file)"
        >×</button>
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

.customer-app-detail-tab-ai-icon {
  display: inline-flex;
  vertical-align: -1px;
  margin-right: 0.2rem;
  color: #8b5cf6;
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

.customer-app-detail-docs-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.customer-app-detail-docs-delete {
  flex-shrink: 0;
  width: 1.8rem;
  height: 1.8rem;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: none;
  color: #777;
  font-size: 1.1rem;
  line-height: 1;
  cursor: pointer;
}

.customer-app-detail-docs-delete:hover:not(:disabled) {
  background: #fbeaea;
  color: #c62828;
}

.customer-app-detail-docs-delete:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.customer-app-detail-docs-item {
  flex: 1;
  min-width: 0;
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
