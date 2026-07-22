<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ChatWindow from './components/ChatWindow.vue'
import StateBar from './components/StateBar.vue'
import ActionButtons from './components/ActionButtons.vue'
import SignalsView from './components/SignalsView.vue'
import ModelsMenu from './components/ModelsMenu.vue'
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

const showSignals = ref(false)
const autoTrackingEnabled = ref(true)
const autoTrackingLoading = ref(false)
const state = ref(null)
const messages = ref([])
const chatLoading = ref(false)
const chatError = ref('')
const chatStatus = ref('')
const actionLoading = ref(false)
const loadError = ref('')
const modelUploadInput = ref(null)
const modelsMenu = ref(null)

// Plain (non-reactive) connection state: the socket itself and the
// resolve/reject pair for whichever chat turn is currently in flight.
let chatSocket = null
let pendingTurn = null

async function loadState() {
  try {
    state.value = await getState()
  } catch (err) {
    loadError.value = `Unable to reach the backend: ${err.message}`
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
    state.value = result.state
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
    state.value = await postAction(actionName)
  } catch (err) {
    loadError.value = err.message
  } finally {
    actionLoading.value = false
  }
}

async function handleReset() {
  if (!window.confirm('Reset the conversation, signals, and transitions? This cannot be undone.')) return
  state.value = await postReset()
  messages.value = []
  chatError.value = ''
  chatStatus.value = ''
  loadError.value = ''
  autoTrackingEnabled.value = true
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
  try {
    const result = await putModel(modelName, file)
    if (!result.success) {
      loadError.value = result.error
      return
    }
    state.value = await getState()
    messages.value = []
    chatError.value = ''
    chatStatus.value = ''
    loadError.value = ''
    autoTrackingEnabled.value = true
    modelsMenu.value?.refresh()
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
  try {
    const result = await activateModel(modelName)
    if (!result.success) {
      loadError.value = result.error
      return
    }
    state.value = await getState()
    messages.value = []
    chatError.value = ''
    chatStatus.value = ''
    loadError.value = ''
    autoTrackingEnabled.value = true
    modelsMenu.value?.refresh()
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
  try {
    await deleteModel(modelName)
    state.value = await getState()
    messages.value = []
    chatError.value = ''
    chatStatus.value = ''
    loadError.value = ''
    autoTrackingEnabled.value = true
    modelsMenu.value?.refresh()
  } catch (err) {
    loadError.value = err.message
  }
}

onMounted(loadState)
onMounted(loadMessages)
onMounted(loadAutoTracking)
onBeforeUnmount(() => {
  chatSocket?.close()
})
</script>

<template>
  <div class="app">
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
