<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ChatWindow from './components/chat/ChatWindow.vue'
import StateBar from './components/StateBar.vue'
import EditProjectView from './components/EditProjectView.vue'
import BenchmarkProjectView from './components/BenchmarkProjectView.vue'
import ProjectsMenu from './components/ProjectsMenu.vue'
import SplashScreen from './components/SplashScreen.vue'
import ErrorBanner from './components/ErrorBanner.vue'
import {
  getState,
  putProject,
  activateProject,
  deleteProject,
  downloadProject,
  getBackup,
  postRestoreBackup
} from './api.js'
import { disconnect as disconnectChat } from './chatClient.js'
import { clearApiError } from './errorStore.js'
import {
  state,
  setCapabilities,
  handleStateChange,
  loadMessages,
  loadAutoTracking,
  loadAiModels,
  clearChatUi,
  sessionsPanelOpen,
  toggleSessionsPanel
} from './chatStore.js'

const showEditProject = ref(false)
const editProjectName = ref(null)
const showBenchmarkProject = ref(false)
const benchmarkProjectName = ref(null)
const modelUploadInput = ref(null)
const projectsMenu = ref(null)

// Initial-boot backend readiness gate — entirely separate from the shared
// error store (which is for runtime errors on an already-running app). 'checking': the
// very first, invisible ping attempt (no splash yet, so a backend that's
// already up never flashes one). 'waiting': the first attempt failed,
// retrying on an interval with the splash visible. 'ready': normal app UI.
// 'failed': retry budget exhausted, explicit error + manual "Retry".
const bootStatus = ref('checking')

const PING_INTERVAL_MS = 800
const PING_TIMEOUT_MS = 3000
const MAX_PING_ATTEMPTS = 30

// Boot-ping bookkeeping. `bootSequenceToken` is bumped by startBootSequence()
// so a stale scheduled retry from a previous sequence (e.g. right after the
// user clicks "Retry") can recognize it's been superseded and no-op instead
// of racing the fresh one.
let pingAttempts = 0
let pingTimeoutHandle = null
let bootSequenceToken = 0

// One ping attempt, bounded by an explicit timeout — plain fetch() never
// times out on its own against a hung connection, and "timeout" is one of
// the failure modes this boot check needs to treat the same as "not ready
// yet". On success, reuses the result directly as the app's current state
// (GET /api/state IS the readiness check — nothing else to fetch for it).
async function pingBackend() {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), PING_TIMEOUT_MS)
  try {
    const newState = await getState(controller.signal)
    setCapabilities({ talkAvailable: newState.talk_enabled ?? true, micAvailable: newState.listen_enabled ?? true })
    handleStateChange(newState)
    return true
  } catch {
    return false
  } finally {
    clearTimeout(timeout)
  }
}

function bootSucceeded() {
  bootStatus.value = 'ready'
  // Clears any error left over from a failed boot-ping retry — that
  // retry loop is invisible UI (see pingBackend), but it goes through the
  // same apiFetch as everything else, so a stale message could otherwise
  // still be sitting in the shared store the moment the chat UI mounts.
  clearApiError()
  loadMessages()
  loadAutoTracking()
  loadAiModels()
  // No proactive chat-socket connect here: chatClient.js connects lazily
  // on the first sendMessage() call, and the opening message (if any) is
  // already covered by loadMessages() above — it's persisted server-side
  // by the time the backend finishes booting, regardless of transport.
}

async function runPingAttempt(token) {
  if (token !== bootSequenceToken) return // superseded by a newer sequence
  pingAttempts++
  const ok = await pingBackend()
  if (token !== bootSequenceToken) return
  if (ok) {
    bootSucceeded()
    return
  }
  if (pingAttempts >= MAX_PING_ATTEMPTS) {
    bootStatus.value = 'failed'
    return
  }
  bootStatus.value = 'waiting'
  pingTimeoutHandle = setTimeout(() => runPingAttempt(token), PING_INTERVAL_MS)
}

// Entry point for both the initial mount and the splash's manual "Retry" —
// restarts the exact same cycle: one immediate, invisible attempt, then
// (only if that one fails) the visible retry loop.
function startBootSequence() {
  bootSequenceToken++
  pingAttempts = 0
  if (pingTimeoutHandle) {
    clearTimeout(pingTimeoutHandle)
    pingTimeoutHandle = null
  }
  bootStatus.value = 'checking'
  runPingAttempt(bootSequenceToken)
}

function triggerModelUpload() {
  modelUploadInput.value?.click()
}

// The fetch-fresh-state-and-redisplay half of the reset/switch/upload/
// delete flow (see chatStore.js's clearChatUi for the optimistic-clear
// half each of those runs first): the fresh state comes from a separate
// GET /api/state call (none of putModel/activateModel/deleteModel's
// responses carry the state payload itself), same as handleReset picks up
// the opening message via REST, regardless of chat transport.
async function refreshStateAndProjects() {
  const newState = await getState()
  projectsMenu.value?.refresh()
  handleStateChange(newState)
  await loadMessages()
}

async function handleModelUploadChange(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // allow re-selecting the same file afterward
  if (!file) return

  const projectName = file.name.replace(/\.(zip|ya?ml)$/i, '')
  clearChatUi()
  try {
    await putProject(projectName, file)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

function handleModelEdit(projectName) {
  editProjectName.value = projectName
  showEditProject.value = true
}

function handleModelBenchmark(projectName) {
  benchmarkProjectName.value = projectName
  showBenchmarkProject.value = true
}

async function handleModelEditSaved() {
  clearChatUi()
  try {
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

// Activation is idempotent backend-side (re-activating the already-active
// model is a no-op, no reset) so this handler doesn't need to
// special-case that itself.
async function handleProjectSwitch(projectName) {
  clearChatUi()
  try {
    await activateProject(projectName)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

// Triggers a browser download from the zip blob — standard synthetic-<a>
// pattern, since fetch() has no way to hand a response straight to the
// browser's own download UI. No UI state changes at all on success: unlike
// switch/upload/delete, downloading doesn't touch the active model or the
// session. On failure, show the error the same way as the rest of the menu.
async function handleModelDownload(projectName) {
  try {
    const blob = await downloadProject(projectName)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${projectName}.zip`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch {
    // already surfaced via apiFetch
  }
}

// Deleting the active model falls back to whatever project's left
// backend-side (or none at all — see the "no-project" splash below), so
// this behaves the same as a successful switch/upload either way — reload
// state, clear the chat.
async function handleModelDelete(projectName) {
  clearChatUi()
  try {
    await deleteProject(projectName)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

// Whole-database download (every project, session, message, signal — not
// scoped to the active project), unlike handleModelDownload's per-project
// zip. No UI state changes on success, same reasoning as that one.
async function handleDownloadBackup() {
  try {
    const blob = await getBackup()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'avance-backup.sqlite'
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    alert('Backup downloaded to your local folder.')
  } catch {
    // already surfaced via apiFetch
  }
}

// Replaces the entire working database server-side — every project,
// session, and message the server currently has is gone either way, so
// this needs the same explicit confirmation as handleReset (chatStore.js),
// then the same reload-everything path as switch/upload/delete.
async function handleRestoreBackup(file) {
  if (!window.confirm('Restore this backup? This replaces the entire working database (all projects, sessions, and messages) and cannot be undone.')) return
  clearChatUi()
  try {
    await postRestoreBackup(file)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

onMounted(startBootSequence)
onBeforeUnmount(() => {
  disconnectChat()
  if (pingTimeoutHandle) clearTimeout(pingTimeoutHandle)
})
</script>

<template>
  <!-- 'checking' (the invisible first ping) renders neither branch, on
       purpose: nothing should flash before we know whether the backend was
       already up. -->
  <SplashScreen v-if="bootStatus === 'waiting'" variant="connecting" />
  <SplashScreen v-else-if="bootStatus === 'failed'" variant="failed" @retry="startBootSequence" />

  <div v-else-if="bootStatus === 'ready'" class="app">
    <header class="topbar">
      <StateBar :state="state" />
      <div class="topbar-actions">
        <button
          type="button"
          class="sessions-btn"
          :class="{ 'sessions-btn-active': sessionsPanelOpen }"
          :disabled="!state?.key"
          title="Sessions"
          @click="toggleSessionsPanel"
        >
          Sessions
        </button>
        <ProjectsMenu
          ref="projectsMenu"
          @select="handleProjectSwitch"
          @edit="handleModelEdit"
          @benchmark="handleModelBenchmark"
          @upload="triggerModelUpload"
          @download="handleModelDownload"
          @delete="handleModelDelete"
          @download-backup="handleDownloadBackup"
          @restore-backup="handleRestoreBackup"
        />
        <input
          ref="modelUploadInput"
          type="file"
          accept=".zip,.yml,.yaml"
          class="upload-model-input"
          @change="handleModelUploadChange"
        />
      </div>
    </header>

    <ErrorBanner />

    <div class="app-body">
      <SplashScreen v-if="!state?.key" variant="no-project" embedded />
      <ChatWindow v-else />
    </div>

    <EditProjectView
      v-if="showEditProject"
      :project-name="editProjectName"
      @close="showEditProject = false"
      @saved="handleModelEditSaved"
      @download="handleModelDownload"
    />

    <BenchmarkProjectView
      v-if="showBenchmarkProject"
      :project-name="benchmarkProjectName"
      @close="showBenchmarkProject = false"
    />
  </div>
</template>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  font-family: system-ui, -apple-system, sans-serif;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  background: #f5f5f7;
  border-bottom: 1px solid #ddd;
}

.app-body {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

.topbar-actions {
  display: flex;
  gap: 0.5rem;
  /* StateBar renders nothing at all when there's no active project (see
     its own v-if="state?.key") — margin-left: auto keeps this pinned to
     the right on its own, rather than relying on .topbar's
     justify-content: space-between, which only works with two flex
     children and collapses to the left with just this one. */
  margin-left: auto;
}

.upload-model-input {
  display: none;
}

.sessions-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.sessions-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.sessions-btn-active {
  background: #4a6fa5;
  color: white;
}

.sessions-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
