<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatWindow from './components/ChatWindow.vue'
import StateBar from './components/StateBar.vue'
import SignalsView from './components/SignalsView.vue'
import EditProjectView from './components/EditProjectView.vue'
import ProjectsMenu from './components/ProjectsMenu.vue'
import SplashScreen from './components/SplashScreen.vue'
import {
  getState,
  putProject,
  activateProject,
  deleteProject,
  downloadProject
} from './api.js'
import { disconnect as disconnectChat } from './chatClient.js'
import { clearApiError } from './errorStore.js'
import {
  state,
  autoTrackingEnabled,
  autoTrackingLoading,
  turnCount,
  setCapabilities,
  handleStateChange,
  loadMessages,
  loadAutoTracking,
  toggleAutoTracking,
  clearChatUi,
  handleReset
} from './chatStore.js'

const showSignals = ref(false)
const showEditProject = ref(false)
const editProjectName = ref(null)
const modelUploadInput = ref(null)
const projectsMenu = ref(null)
const signalsView = ref(null)

// Signals only ever change server-side as a result of auto-tracking inside
// a chat turn/action, or wholesale when the active model itself changes
// (reset/switch/upload/delete) — called from every one of those spots so
// the docked inspector (see SignalsView) tracks the backend live instead
// of only refreshing when reopened. A no-op while the panel is closed.
function refreshSignalsIfOpen() {
  if (showSignals.value) signalsView.value?.refresh()
}

// chatStore.js bumps this at the end of every chat turn/action/reset —
// from either chat view, main or embedded (see EditProjectView.vue) —
// since a signal value can shift even without a state change. Decouples
// the store from knowing anything about this app's own docked panel.
watch(turnCount, refreshSignalsIfOpen)

// Draggable split between the chat and the docked Signals panel (see
// SignalsView's own >=900px breakpoint — the resizer itself is hidden
// below that width via the same media query, so this only ever matters
// in docked mode). Width persists across reloads; a fresh session with no
// saved width falls back to the panel's own default (400px, in its CSS).
const SIGNALS_MIN_WIDTH = 280
const SIGNALS_MAX_WIDTH = 720
const CHAT_MIN_WIDTH = 320
const appBody = ref(null)
const resizingSignals = ref(false)
const signalsPanelWidth = ref(Number(localStorage.getItem('signalsPanelWidth')) || 400)

function startSignalsResize(event) {
  resizingSignals.value = true
  event.target.setPointerCapture(event.pointerId)
}

function onSignalsResizeMove(event) {
  if (!resizingSignals.value || !appBody.value) return
  const rect = appBody.value.getBoundingClientRect()
  const maxWidth = Math.min(SIGNALS_MAX_WIDTH, rect.width - CHAT_MIN_WIDTH)
  const newWidth = rect.right - event.clientX
  signalsPanelWidth.value = Math.min(maxWidth, Math.max(SIGNALS_MIN_WIDTH, newWidth))
}

function stopSignalsResize(event) {
  if (!resizingSignals.value) return
  resizingSignals.value = false
  event.target.releasePointerCapture(event.pointerId)
  localStorage.setItem('signalsPanelWidth', String(signalsPanelWidth.value))
}

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
  refreshSignalsIfOpen()
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

// Deleting the active model always falls back to "default" backend-side, so
// this behaves the same as a successful switch/upload — reload state, clear
// the chat.
async function handleModelDelete(projectName) {
  clearChatUi()
  try {
    await deleteProject(projectName)
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
  <SplashScreen v-if="bootStatus === 'waiting'" />
  <SplashScreen v-else-if="bootStatus === 'failed'" failed @retry="startBootSequence" />

  <div v-else-if="bootStatus === 'ready'" class="app">
    <header class="topbar">
      <StateBar :state="state" />
      <div class="topbar-actions">
        <button
          class="signals-btn"
          :class="{ 'signals-btn-on': showSignals }"
          @click="showSignals = !showSignals"
        >
          Signals
        </button>
        <ProjectsMenu
          ref="projectsMenu"
          @select="handleProjectSwitch"
          @edit="handleModelEdit"
          @upload="triggerModelUpload"
          @download="handleModelDownload"
          @delete="handleModelDelete"
        />
        <input
          ref="modelUploadInput"
          type="file"
          accept=".zip,.yml,.yaml"
          class="upload-model-input"
          @change="handleModelUploadChange"
        />
        <button class="reset-btn" @click="handleReset">Reset</button>
      </div>
    </header>

    <div class="app-body" ref="appBody" :class="{ 'app-body-resizing': resizingSignals }">
      <ChatWindow />

      <!-- Draggable split, docked mode only (hidden below the same
           >=900px breakpoint SignalsView docks at — see its own CSS). -->
      <div
        v-if="showSignals"
        class="signals-resizer"
        @pointerdown="startSignalsResize"
        @pointermove="onSignalsResizeMove"
        @pointerup="stopSignalsResize"
        @pointercancel="stopSignalsResize"
      ></div>

      <!-- Wide screens: docked inspector beside the chat, stays open across
           turns and live-refreshes (see refreshSignalsIfOpen). Narrow
           screens: SignalsView's own CSS falls back to a full-screen modal
           (the inline width below is harmless there — inset:0 wins). -->
      <SignalsView
        v-if="showSignals"
        ref="signalsView"
        :style="{ width: signalsPanelWidth + 'px' }"
        :state="state"
        :auto-tracking-enabled="autoTrackingEnabled"
        :auto-tracking-loading="autoTrackingLoading"
        @close="showSignals = false"
        @toggle-auto-tracking="toggleAutoTracking"
      />
    </div>

    <EditProjectView
      v-if="showEditProject"
      :project-name="editProjectName"
      @close="showEditProject = false"
      @saved="handleModelEditSaved"
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

/* While dragging, the pointer can slide over the chat/panel iframe-less
   content faster than it moves — force the resize cursor and kill text
   selection everywhere so the drag doesn't feel like it "catches" on
   message bubbles. */
.app-body-resizing {
  cursor: col-resize;
  user-select: none;
}

.app-body-resizing :deep(*) {
  cursor: col-resize !important;
  user-select: none !important;
}

.signals-resizer {
  display: none;
  flex: none;
  width: 6px;
  margin: 0 -3px;
  cursor: col-resize;
  touch-action: none;
  position: relative;
  z-index: 1;
}

.signals-resizer::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 2px;
  right: 2px;
  border-radius: 2px;
}

.signals-resizer:hover::after {
  background: #4a6fa5;
}

@media (min-width: 900px) {
  .signals-resizer {
    display: block;
  }
}

.topbar-actions {
  display: flex;
  gap: 0.5rem;
}

.signals-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.signals-btn:hover {
  background: #4a6fa5;
  color: white;
}

.signals-btn-on {
  background: #4a6fa5;
  color: white;
}

.upload-model-input {
  display: none;
}

.reset-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
}

.reset-btn:hover {
  background: #c62828;
  color: white;
}
</style>
