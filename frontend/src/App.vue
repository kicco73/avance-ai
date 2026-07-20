<script setup>
import { onMounted, ref } from 'vue'
import ChatWindow from './components/ChatWindow.vue'
import StateBar from './components/StateBar.vue'
import ActionButtons from './components/ActionButtons.vue'
import { getState, postChat, postAction, postReset } from './api.js'

const MAX_RETRIES = 5
const BASE_DELAY_MS = 1000

const state = ref(null)
const messages = ref([])
const chatLoading = ref(false)
const chatError = ref('')
const chatStatus = ref('')
const actionLoading = ref(false)
const loadError = ref('')

async function loadState() {
  try {
    state.value = await getState()
  } catch (err) {
    loadError.value = `Unable to reach the backend: ${err.message}`
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// Counts down out loud in chatStatus so the "..." prompt reflects the backoff.
async function waitWithCountdown(attempt, waitMs) {
  let remainingSeconds = Math.ceil(waitMs / 1000)
  while (remainingSeconds > 0) {
    chatStatus.value = `Service unavailable, retrying (${attempt}/${MAX_RETRIES}) in ${remainingSeconds}s...`
    await sleep(1000)
    remainingSeconds -= 1
  }
}

// Sends a chat message, retrying with exponential backoff on 503 (upstream
// model API overloaded). Gives up after MAX_RETRIES retries.
async function sendWithRetry(text) {
  let attempt = 0
  while (true) {
    try {
      return await postChat(text)
    } catch (err) {
      if (err.status !== 503 || attempt >= MAX_RETRIES) {
        throw err
      }
      attempt += 1
      const waitMs = BASE_DELAY_MS * 2 ** (attempt - 1)
      await waitWithCountdown(attempt, waitMs)
    }
  }
}

async function submitMessage(message) {
  chatError.value = ''
  message.failed = false
  chatLoading.value = true
  try {
    const res = await sendWithRetry(message.content)
    messages.value.push({ role: 'assistant', content: res.reply })
    state.value = res.state
  } catch (err) {
    message.failed = true
    chatError.value =
      err.status === 503
        ? 'Service still unavailable after the maximum number of retries. Tap the icon next to your message to try again.'
        : err.message
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
</script>

<template>
  <div class="app">
    <header class="topbar">
      <h1>Avance — Prototype</h1>
      <button class="reset-btn" @click="handleReset">Reset</button>
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
