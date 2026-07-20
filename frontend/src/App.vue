<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ChatWindow from './components/ChatWindow.vue'
import StateBar from './components/StateBar.vue'
import ActionButtons from './components/ActionButtons.vue'
import SignalsView from './components/SignalsView.vue'
import { getState, createChatSocket, postAction, postReset } from './api.js'

const showSignals = ref(false)
const state = ref(null)
const messages = ref([])
const chatLoading = ref(false)
const chatError = ref('')
const chatStatus = ref('')
const actionLoading = ref(false)
const loadError = ref('')

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
  state.value = await postReset()
  messages.value = []
  chatError.value = ''
  chatStatus.value = ''
  loadError.value = ''
}

onMounted(loadState)
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
        <button class="reset-btn" @click="handleReset">Reset</button>
      </div>
    </header>

    <p class="load-error" v-if="loadError">{{ loadError }}</p>

    <ChatWindow
      :messages="messages"
      :loading="chatLoading"
      :status="chatStatus"
      :error="chatError"
      @send="handleSend"
      @resend="handleResend"
    />

    <ActionButtons
      :actions="state?.actions ?? []"
      :disabled="actionLoading"
      @action="handleAction"
    />

    <StateBar :state="state" />

    <SignalsView v-if="showSignals" @close="showSignals = false" />
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
