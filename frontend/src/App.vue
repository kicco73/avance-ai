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
  createChatSocket,
  postAction,
  getAutoTracking,
  postAutoTracking,
  postReset,
  putModel,
  activateModel,
  deleteModel,
  downloadModel
} from './api.js'
import { playMessageChime } from './audio.js'
import { celebrate } from './confetti.js'

const showSignals = ref(false)
const autoTrackingEnabled = ref(true)
const autoTrackingLoading = ref(false)
const state = ref(null)
const messages = ref([])
const historyLoaded = ref(false)
const chatLoading = ref(false)
const chatError = ref('')
const chatStatus = ref('')
const actionLoading = ref(false)
const loadError = ref('')
const modelUploadInput = ref(null)
const modelsMenu = ref(null)

// Initial-boot backend readiness gate — entirely separate from loadError
// (which is for runtime errors on an already-running app). 'checking': the
// very first, invisible ping attempt (no splash yet, so a backend that's
// already up never flashes one). 'waiting': the first attempt failed,
// retrying on an interval with the splash visible. 'ready': normal app UI.
// 'failed': retry budget exhausted, explicit error + manual "Retry".
const bootStatus = ref('checking')

const PING_INTERVAL_MS = 800
const PING_TIMEOUT_MS = 3000
const MAX_PING_ATTEMPTS = 30

// Plain (non-reactive) connection state: the socket itself and the
// resolve/reject pair for whichever chat turn is currently in flight.
let chatSocket = null
let pendingTurn = null

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
  loadMessages()
  loadAutoTracking()
  ensureChatSocket()
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
  } catch (err) {
    loadError.value = err.message
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
  } catch (err) {
    loadError.value = err.message
  }
}

async function toggleAutoTracking() {
  autoTrackingLoading.value = true
  try {
    const res = await postAutoTracking(!autoTrackingEnabled.value)
    autoTrackingEnabled.value = res.enabled
  } catch (err) {
    loadError.value = err.message
  } finally {
    autoTrackingLoading.value = false
  }
}

// All retry/backoff decisions happen server-side; this only renders
// whatever the backend pushes ('retrying' ticks, then 'done'/'failed').
function handleSocketMessage(event) {
  const data = JSON.parse(event.data)

  if (data.type === 'retrying') {
    const seconds = Math.max(0, Math.ceil(data.retry_in ?? 0))
    chatStatus.value = `Service unavailable, retrying (${data.attempt}/${data.max_attempts}) in ${seconds}s...`
    return
  }

  if (data.type === 'message') {
    // The AI-opens-the-conversation push (reset/activate/upload/delete):
    // unprompted by any pendingTurn, so it's handled independently of the
    // resolve/reject flow below. The local array was already emptied
    // optimistically by whichever handler triggered this, before it even
    // sent its REST request — so this is always appended into a clean slate,
    // regardless of whether this frame beat that request's response or not.
    messages.value.push({ role: 'assistant', content: data.reply, failed: false })
    playMessageChime()
    handleStateChange(data.state)
    return
  }

  if (!pendingTurn) return
  const { resolve, reject } = pendingTurn
  pendingTurn = null
  if (data.type === 'done') {
    resolve(data)
  } else {
    reject(new Error(data.error || 'Chat request failed.'))
  }
}

function openChatSocket() {
  return new Promise((resolve, reject) => {
    const socket = createChatSocket()
    socket.onopen = () => resolve(socket)
    socket.onmessage = handleSocketMessage
    socket.onerror = () => reject(new Error('Unable to connect to the chat service.'))
    socket.onclose = () => {
      if (chatSocket === socket) chatSocket = null
      pendingTurn?.reject(new Error('Chat connection closed.'))
      pendingTurn = null
    }
    chatSocket = socket
  })
}

async function getOpenSocket() {
  if (chatSocket && chatSocket.readyState === WebSocket.OPEN) return chatSocket
  return openChatSocket()
}

// Opens the chat socket proactively once the backend is known reachable,
// instead of waiting for the first message send — the opening-message push
// (reset/activate/upload/delete) can arrive at any time, regardless of
// which view (chat/Models/Signals) is currently on screen, so the
// connection has to already be there rather than lazily created on demand.
function ensureChatSocket() {
  if (chatSocket && chatSocket.readyState <= WebSocket.OPEN) return // CONNECTING or OPEN
  openChatSocket().catch(() => {
    // A failed proactive connect isn't fatal here: getOpenSocket() retries
    // on the next actual chat turn, and a push missed while disconnected
    // isn't lost either — the message is already persisted server-side.
  })
}

// Sends one chat turn over the websocket and waits for the backend's
// terminal push (done/failed) — no polling involved.
async function runChatTurn(text) {
  const socket = await getOpenSocket()
  return new Promise((resolve, reject) => {
    pendingTurn = { resolve, reject }
    socket.send(JSON.stringify({ message: text }))
  })
}

async function submitMessage(message) {
  chatError.value = ''
  message.failed = false
  chatLoading.value = true
  try {
    const result = await runChatTurn(message.content)
    messages.value.push({ role: 'assistant', content: result.reply })
    // Only for a freshly arrived AI reply — never for the user's own sent
    // message, and never for history loaded at boot/reset (this only ever
    // runs from a live chat turn just completing).
    playMessageChime()
    handleStateChange(result.state)
  } catch (err) {
    message.failed = true
    chatError.value = err.message
  } finally {
    chatLoading.value = false
    chatStatus.value = ''
  }
}

async function handleSend(text) {
  const message = { role: 'user', content: text, failed: false }
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
  } catch (err) {
    loadError.value = err.message
  } finally {
    actionLoading.value = false
  }
}

async function handleReset() {
  if (!window.confirm('Reset the conversation, signals, and transitions? This cannot be undone.')) return
  // Emptied before the request is even sent, not after the response comes
  // back: the opening-message push over the websocket can arrive before,
  // during, or after this REST call resolves, so there's no "clear, then
  // the push arrives and gets wiped" race — whenever it lands, the array is
  // already the empty slate it expects to be appended into.
  messages.value = []
  chatError.value = ''
  chatStatus.value = ''
  loadError.value = ''
  autoTrackingEnabled.value = true
  const newState = await postReset()
  state.value = null
  handleStateChange(newState)
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
  // See handleReset: emptied before the request, not after — so an
  // opening-message push arriving anytime around this call always lands in
  // an already-empty array.
  messages.value = []
  chatError.value = ''
  chatStatus.value = ''
  loadError.value = ''
  autoTrackingEnabled.value = true
  try {
    const result = await putModel(modelName, file)
    if (!result.success) {
      loadError.value = result.error
      return
    }
    const newState = await getState()
    modelsMenu.value?.refresh()
    handleStateChange(newState)
  } catch (err) {
    loadError.value = err.message
  }
}

// Same post-success behavior as upload/Reset: reload state, clear the
// displayed chat. On failure, show the error via the same loadError banner
// used for upload failures, without touching anything else. Activation is
// idempotent backend-side (re-activating the already-active model is a
// no-op, no reset) so this handler doesn't need to special-case that itself.
async function handleModelSwitch(modelName) {
  // See handleReset: emptied before the request, not after.
  messages.value = []
  chatError.value = ''
  chatStatus.value = ''
  loadError.value = ''
  autoTrackingEnabled.value = true
  try {
    const result = await activateModel(modelName)
    if (!result.success) {
      loadError.value = result.error
      return
    }
    const newState = await getState()
    modelsMenu.value?.refresh()
    handleStateChange(newState)
  } catch (err) {
    loadError.value = err.message
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
  } catch (err) {
    loadError.value = err.message
  }
}

// Deleting the active model always falls back to "default" backend-side, so
// this behaves the same as a successful switch/upload — reload state, clear
// the chat. Unlike switch/upload, failures here surface as a thrown Error
// (see deleteModel), not a {success: false} value.
async function handleModelDelete(modelName) {
  // See handleReset: emptied before the request, not after.
  messages.value = []
  chatError.value = ''
  chatStatus.value = ''
  loadError.value = ''
  autoTrackingEnabled.value = true
  try {
    await deleteModel(modelName)
    const newState = await getState()
    handleStateChange(newState)
    modelsMenu.value?.refresh()
  } catch (err) {
    loadError.value = err.message
  }
}

onMounted(startBootSequence)
onBeforeUnmount(() => {
  chatSocket?.close()
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

    <p class="load-error" v-if="loadError">{{ loadError }}</p>

    <ChatWindow
      :messages="messages"
      :loading="chatLoading"
      :status="chatStatus"
      :error="chatError"
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

.load-error {
  color: #c62828;
  padding: 0.5rem 1rem;
  margin: 0;
}
</style>
