<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ChatWindow from './components/chat/ChatWindow.vue'
import StateBar from './components/StateBar.vue'
import EditProjectView from './components/project/edit/EditProjectView.vue'
import LabelProjectView from './components/project/label/LabelProjectView.vue'
import ProjectsMenu from './components/ProjectsMenu.vue'
import SettingsMenu from './components/settings/SettingsMenu.vue'
import ManageProjectsView from './components/settings/ManageProjectsView.vue'
import SplashScreen from './components/SplashScreen.vue'
import ErrorBanner from './components/ErrorBanner.vue'
import ToastContainer from './components/ToastContainer.vue'
import {
  getState,
  putProject,
  postNewProject,
  activateProject,
  deleteProject,
  downloadProject,
  getBackup,
  postRestoreBackup,
  postPublishProject
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
  projectPaused,
  projectPausedReason
} from './chatStore.js'

const showEditProject = ref(false)
const editProjectName = ref(null)
const showBenchmarkProject = ref(false)
const benchmarkProjectName = ref(null)
const showManageProjects = ref(false)
const modelUploadInput = ref(null)
const projectsMenu = ref(null)
const manageProjectsView = ref(null)

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
  manageProjectsView.value?.refresh()
  handleStateChange(newState)
  await loadMessages()
}

// "New project": same server-side effect as picking samples/Hello
// world.zip in the upload dialog (see postNewProject), so it reloads
// state the same way a real upload does — including auto-publishing (see
// handleModelUploadChange's own identical reasoning below): a freshly
// created project has never been published either, so without this it'd
// look usable right away but couldn't actually chat yet.
async function handleNewProject() {
  clearChatUi()
  try {
    const result = await postNewProject()
    await postPublishProject(result.project_name)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

async function handleModelUploadChange(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // allow re-selecting the same file afterward
  if (!file) return

  const projectName = file.name.replace(/\.(zip|ya?ml)$/i, '')
  clearChatUi()
  try {
    await putProject(projectName, file)
    // A freshly uploaded project has never been published — nothing can
    // chat with it yet (see db.create_chat_session, which requires a
    // published_revision) until someone opens "Edit project" and clicks
    // Publish. Doing that automatically here means an upload is usable
    // right away, same as it always visibly appeared to be.
    await postPublishProject(projectName)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

// EditProjectView.vue's own embedded "Test" chat creates its draft
// session against whichever project is currently *active* server-side
// (see ChatService.create_draft_session — it never actually looks at the
// URL's own project_name), an invariant every previous way into "Edit
// project" upheld for free: before Manage projects made a project's own
// row directly clickable, the only path in was ProjectsMenu.vue's own
// "Edit project" item, which only ever edited whichever project was
// already active. That's no longer guaranteed — Manage projects lets you
// open Edit for a project that isn't active at all — so this activates
// `projectName` first, same as a real project switch, before ever
// opening the view: without this, Test silently runs against whatever
// project was active before, not the one actually being edited.
async function handleModelEdit(projectName) {
  clearChatUi()
  try {
    await activateProject(projectName)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
  editProjectName.value = projectName
  showEditProject.value = true
}

function handleManageProjectsEdit(projectName) {
  showManageProjects.value = false
  handleModelEdit(projectName)
}

function handleManageProjectsBenchmark(projectName) {
  showManageProjects.value = false
  handleModelBenchmark(projectName)
}

// Edit/Label are only ever opened from Manage projects now (ProjectsMenu.vue
// no longer has its own entry points into either) — so "Back" out of them
// returns there rather than to the main chat view.
function closeEditProject() {
  showEditProject.value = false
  showManageProjects.value = true
}

function closeBenchmarkProject() {
  showBenchmarkProject.value = false
  showManageProjects.value = true
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
    // After the download actually happened, never before (see
    // handleDownloadBackup's own same-order notice) — shared by both the
    // Projects menu's own "Download" item and EditProjectView.vue's own
    // toolbar button, both wired to this same handler.
    alert(`Project "${projectName}" downloaded to your local folder.`)
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
  <ToastContainer />

  <SplashScreen v-if="bootStatus === 'waiting'" variant="connecting" />
  <SplashScreen v-else-if="bootStatus === 'failed'" variant="failed" @retry="startBootSequence" />

  <div v-else-if="bootStatus === 'ready'" class="app">
    <header class="topbar">
      <StateBar :state="state" />
      <div class="topbar-actions">
        <ProjectsMenu
          ref="projectsMenu"
          @select="handleProjectSwitch"
          @download="handleModelDownload"
        />
        <SettingsMenu
          @manage-projects="showManageProjects = true"
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
      <SplashScreen v-if="projectPaused" variant="paused" :reason="projectPausedReason" embedded />
      <SplashScreen v-else-if="!state?.key" variant="no-project" embedded />
      <ChatWindow v-else />
    </div>

    <EditProjectView
      v-if="showEditProject"
      :project-name="editProjectName"
      @close="closeEditProject"
      @saved="handleModelEditSaved"
      @download="handleModelDownload"
    />

    <LabelProjectView
      v-if="showBenchmarkProject"
      :project-name="benchmarkProjectName"
      @close="closeBenchmarkProject"
    />

    <ManageProjectsView
      v-if="showManageProjects"
      ref="manageProjectsView"
      @close="showManageProjects = false"
      @new-project="handleNewProject"
      @upload="triggerModelUpload"
      @delete="handleModelDelete"
      @edit="handleManageProjectsEdit"
      @benchmark="handleManageProjectsBenchmark"
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

</style>
