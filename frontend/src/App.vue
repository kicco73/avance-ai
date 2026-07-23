<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import ChatWindow from './components/ChatWindow.vue'
import StateBar from './components/StateBar.vue'
import ActionButtons from './components/ActionButtons.vue'
import SignalsView from './components/SignalsView.vue'
import ModelsMenu from './components/ModelsMenu.vue'
import SplashScreen from './components/SplashScreen.vue'
import {
  getState,
  getMessages,
  postAction,
  getAutoTracking,
  postAutoTracking,
  postReset,
  putModel,
  activateModel,
  deleteModel,
  downloadModel
} from './api.js'
import { disconnect as disconnectChat, sendMessage } from './chatClient.js'
import { playMessageChime } from './audio.js'
import { celebrate } from './confetti.js'
import { clearApiError, errorDetail, errorMessage } from './errorStore.js'

const showSignals = ref(false)
const autoTrackingEnabled = ref(true)
const autoTrackingLoading = ref(false)
const state = ref(null)
const messages = ref([])
const historyLoaded = ref(false)
const chatLoading = ref(false)
const chatStatus = ref('')
const actionLoading = ref(false)
const modelUploadInput = ref(null)
const modelsMenu = ref(null)

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

// Stable id assigned to every user message at creation, so its status can
// be found and updated by identity later — never by mutating whatever
// object reference the caller happened to capture (see submitMessage: a
// direct mutation on that captured reference bypasses Vue's reactive
// array proxy entirely, so the UI doesn't update until some unrelated
// change happens to force a re-render).
let nextMessageId = 0

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

function handleStateChange(newState) {
  const changed = state.value?.key !== newState.key
  state.value = newState
  // Only on an actual transition into the state, never on a redundant
  // re-fetch of the one we're already in (e.g. the boot ping, or any other
  // GET /api/state call that happens to return the same state) — otherwise
  // celebrate() would refire every time this runs.
  if (changed && newState?.on_enter === 'celebrate') {
    celebrate()
  }
}

// Redisplays whatever conversation the backend already persisted (e.g.
// across a backend restart) — session.history server-side is otherwise only
// ever used internally to build LLM calls, never pushed to the client.
async function loadMessages() {
  try {
    const history = await getMessages()
    messages.value = history.map((m) => ({ role: m.role, content: m.content, failed: false }))
  } catch {
    // already surfaced via apiFetch
  } finally {
    // Gates ChatWindow's bump-in animation (see its historyLoaded prop):
    // this hydration is async, so it lands well after ChatWindow has
    // already mounted — without this flag every history row would still
    // read as "just added" the moment it arrives. Setting `messages` and
    // `historyLoaded` in the very same tick isn't enough on its own: Vue
    // batches both changes into one render, so TransitionGroup would see
    // the *new* name already in effect for the very update that adds the
    // history rows. Waiting a tick lets that first render (still gated by
    // the old, unstyled name) flush before the flag flips.
    await nextTick()
    historyLoaded.value = true
  }
}

async function loadAutoTracking() {
  try {
    const res = await getAutoTracking()
    autoTrackingEnabled.value = res.enabled
  } catch {
    // already surfaced via apiFetch
  }
}

async function toggleAutoTracking() {
  autoTrackingLoading.value = true
  try {
    const res = await postAutoTracking(!autoTrackingEnabled.value)
    autoTrackingEnabled.value = res.enabled
  } catch {
    // already surfaced via apiFetch
  } finally {
    autoTrackingLoading.value = false
  }
}

// Looks the message back up by id through the reactive `messages` array
// (rather than mutating whatever reference the caller passed in) so the
// assignment goes through Vue's reactive proxy and updates the UI
// immediately — see the note by `nextMessageId` above.
function setMessageFailed(id, failed) {
  const target = messages.value.find((m) => m.id === id)
  if (target) target.failed = failed
}

async function submitMessage(message) {
  clearApiError()
  setMessageFailed(message.id, false)
  chatLoading.value = true
  try {
    // sendMessage() (chatClient.js) tries the websocket first and falls
    // back to REST transparently — this code never knows which one ran.
    const result = await sendMessage(message.content, {
      onStatus: (text) => { chatStatus.value = text }
    })
    messages.value.push({ role: 'assistant', content: result.reply })
    // Only for a freshly arrived AI reply — never for the user's own sent
    // message, and never for history loaded at boot/reset (this only ever
    // runs from a live chat turn just completing).
    playMessageChime()
    handleStateChange(result.state)
  } catch {
    // Already surfaced via the websocket handler or apiFetch (see
    // chatClient.js) — this only has to update this specific message's
    // own status, synchronously with the outcome.
    setMessageFailed(message.id, true)
  } finally {
    chatLoading.value = false
    chatStatus.value = ''
  }
}

async function handleSend(text) {
  const message = { id: ++nextMessageId, role: 'user', content: text, failed: false }
  messages.value.push(message)
  await submitMessage(message)
}

async function handleResend(index) {
  if (chatLoading.value) return
  const message = messages.value[index]
  if (!message || message.role !== 'user') return
  await submitMessage(message)
}

async function handleAction(actionName) {
  actionLoading.value = true
  try {
    const newState = await postAction(actionName)
    handleStateChange(newState)
  } catch {
    // already surfaced via apiFetch
  } finally {
    actionLoading.value = false
  }
}

async function handleReset() {
  if (!window.confirm('Reset the conversation, signals, and transitions? This cannot be undone.')) return
  messages.value = []
  clearApiError()
  chatStatus.value = ''
  autoTrackingEnabled.value = true
  try {
    const newState = await postReset()
    state.value = null
    handleStateChange(newState)
    // The backend generates + persists the opening message synchronously
    // as part of postReset() above (see chat_service.open_if_needed) —
    // reloading history here picks it up via REST, regardless of whether
    // a chat websocket is even connected.
    await loadMessages()
  } catch {
    // already surfaced via apiFetch
  }
}

function triggerModelUpload() {
  modelUploadInput.value?.click()
}

// On success, behaves exactly like Reset — except the fresh state comes from
// a separate GET /api/state call, since the PUT response only carries
// {success, model_name}, not the state payload itself.
async function handleModelUploadChange(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // allow re-selecting the same file afterward
  if (!file) return

  const modelName = file.name.replace(/\.(zip|ya?ml)$/i, '')
  messages.value = []
  clearApiError()
  chatStatus.value = ''
  autoTrackingEnabled.value = true
  try {
    await putModel(modelName, file)
    const newState = await getState()
    modelsMenu.value?.refresh()
    handleStateChange(newState)
    // See handleReset: picks up the opening message via REST, regardless
    // of chat transport.
    await loadMessages()
  } catch {
    // already surfaced via apiFetch
  }
}

// Same post-success behavior as upload/Reset: reload state, clear the
// displayed chat. Activation is idempotent backend-side (re-activating the
// already-active model is a no-op, no reset) so this handler doesn't need
// to special-case that itself.
async function handleModelSwitch(modelName) {
  messages.value = []
  clearApiError()
  chatStatus.value = ''
  autoTrackingEnabled.value = true
  try {
    await activateModel(modelName)
    const newState = await getState()
    modelsMenu.value?.refresh()
    handleStateChange(newState)
    // See handleReset: picks up the opening message via REST, regardless
    // of chat transport.
    await loadMessages()
  } catch {
    // already surfaced via apiFetch
  }
}

// Triggers a browser download from the zip blob — standard synthetic-<a>
// pattern, since fetch() has no way to hand a response straight to the
// browser's own download UI. No UI state changes at all on success: unlike
// switch/upload/delete, downloading doesn't touch the active model or the
// session. On failure, show the error the same way as the rest of the menu.
async function handleModelDownload(modelName) {
  try {
    const blob = await downloadModel(modelName)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${modelName}.zip`
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
async function handleModelDelete(modelName) {
  messages.value = []
  clearApiError()
  chatStatus.value = ''
  autoTrackingEnabled.value = true
  try {
    await deleteModel(modelName)
    const newState = await getState()
    handleStateChange(newState)
    modelsMenu.value?.refresh()
    // See handleReset: picks up the opening message via REST, regardless
    // of chat transport.
    await loadMessages()
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
      <h1>Avance — Prototype</h1>
      <div class="topbar-actions">
        <button class="signals-btn" @click="showSignals = true">Signals</button>
        <ModelsMenu
          ref="modelsMenu"
          @select="handleModelSwitch"
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

    <ChatWindow
      :messages="messages"
      :loading="chatLoading"
      :status="chatStatus"
      :error-message="errorMessage"
      :error-detail="errorDetail"
      :final-state-reached="state?.final ?? false"
      :history-loaded="historyLoaded"
      @send="handleSend"
      @resend="handleResend"
    />

    <ActionButtons
      :actions="state?.actions ?? []"
      :disabled="actionLoading"
      :auto-tracking-enabled="autoTrackingEnabled"
      @action="handleAction"
    />

    <StateBar :state="state" />

    <SignalsView
      v-if="showSignals"
      :state="state"
      :auto-tracking-enabled="autoTrackingEnabled"
      :auto-tracking-loading="autoTrackingLoading"
      @close="showSignals = false"
      @toggle-auto-tracking="toggleAutoTracking"
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
  border-bottom: 1px solid #ddd;
}

.topbar h1 {
  font-size: 1.1rem;
  margin: 0;
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
