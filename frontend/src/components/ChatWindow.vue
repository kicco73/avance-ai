<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ActionButtons from './ActionButtons.vue'
import MessageBubble from './MessageBubble.vue'
import SessionsPanel from './SessionsPanel.vue'
import { errorDetail, errorMessage, setApiError } from '../errorStore.js'
import { startRecording, stopRecording } from '../mic.js'
import {
  state,
  messages,
  chatLoading,
  chatStatus,
  actionLoading,
  autoTrackingEnabled,
  audioEnabled,
  talkAvailable,
  micAvailable,
  spokenTextEnabled,
  draft,
  currentSessionId,
  selectedSessionActive,
  sessions,
  sessionsLoading,
  sessionsPanelOpen,
  selectSession,
  handleNewSession,
  handleDeleteSession,
  handleSend,
  handleResend,
  handleVoiceMessage,
  handleAction,
  toggleAudio,
  toggleSpokenText
} from '../chatStore.js'

const scrollEl = ref(null)
const inputEl = ref(null)
const showErrorDetail = ref(false)
const recording = ref(false)
const deletingSessionId = ref(null)

async function onDeleteSession(session) {
  deletingSessionId.value = session.id
  try {
    await handleDeleteSession(session)
  } finally {
    deletingSessionId.value = null
  }
}

// No `state.value.key` means there's no active project/state at all (see
// controller.py's GET /api/state, which returns just the talk/listen
// flags in that case) — chat must stay disabled rather than defaulting
// to enabled. selectedSessionActive reflects the backend's own "active"
// verdict for whichever session is currently displayed (see chatStore.js's
// selectSession) — never recomputed here from a timestamp. A session can
// be individually open (not expired) without being this one: only the
// single most recently started open session per project is ever active
// (see ChatSessionManager), every other one is inactive regardless of
// its own open/closed status.
const chatDisabled = computed(() => !state.value?.key || !state.value?.chat || !selectedSessionActive.value)

const chatDisabledReason = computed(() => {
  if (!selectedSessionActive.value) return 'This session is no longer active.'
  return 'Please select:'
})

// Draggable divider between the sessions panel and the chat itself (same
// mousedown/movementX pattern as EditProjectView.vue's own split panes).
const sessionsWidth = ref(240)
let draggingSessions = false

function startSessionsDrag(event) {
  draggingSessions = true
  event.preventDefault()
}

function onSessionsDrag(event) {
  if (!draggingSessions) return
  sessionsWidth.value = Math.min(420, Math.max(160, sessionsWidth.value + event.movementX))
}

function stopSessionsDrag() {
  draggingSessions = false
}

onMounted(() => {
  window.addEventListener('mousemove', onSessionsDrag)
  window.addEventListener('mouseup', stopSessionsDrag)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onSessionsDrag)
  window.removeEventListener('mouseup', stopSessionsDrag)
})

watch(errorMessage, () => {
  showErrorDetail.value = false
})

function submit() {
  const text = draft.value.trim()
  if (!text || chatLoading.value || chatDisabled.value) return
  handleSend(text)
  draft.value = ''
}

function resend(i) {
  if (chatLoading.value) return
  handleResend(i)
}

async function startPtt(event) {
  if (event.pointerType === 'mouse' && event.button !== 0) return
  if (recording.value || chatLoading.value || chatDisabled.value) return
  try {
    await startRecording()
    recording.value = true
  } catch (err) {
    setApiError('Microphone access was denied.', err.message)
  }
}

async function stopPtt() {
  if (!recording.value) return
  recording.value = false
  const blob = await stopRecording()
  if (blob?.size) handleVoiceMessage(blob)
}

function scrollToBottom() {
  nextTick(() => {
    if (scrollEl.value) {
      scrollEl.value.scrollTop = scrollEl.value.scrollHeight
    }
  })
}

// Scroll in automatico quando arrivano nuovi messaggi o aggiornamenti in streaming
watch(
  messages,
  () => {
    scrollToBottom()
  },
  { deep: true }
)

watch(chatLoading, async (isLoading, wasLoading) => {
  if (isLoading || !wasLoading || chatDisabled.value) return

  await nextTick()
  inputEl.value?.focus()
})

function focusInput() {
  if (chatDisabled.value) return
  inputEl.value?.focus()
}

async function onAction(actionName) {
  await handleAction(actionName)
  await nextTick()
  focusInput()
}
</script>

<template>
  <div class="chat-window-shell">
    <Transition name="panel-slide-left">
    <div v-if="sessionsPanelOpen" class="sessions-panel-wrap">
      <div class="sessions-panel" :style="{ width: sessionsWidth + 'px' }">
        <SessionsPanel
          :sessions="sessions"
          :loading="sessionsLoading"
          :current-session-id="currentSessionId"
          :deleting-session-id="deletingSessionId"
          @select="selectSession"
          @create="handleNewSession"
          @delete="onDeleteSession"
        />
      </div>

      <div class="split-divider" @mousedown="startSessionsDrag"></div>
    </div>
    </Transition>

    <div class="chat-window">
    <div class="messages" ref="scrollEl">
      <MessageBubble
        v-for="(msg, i) in messages"
        :key="msg.messageId || msg.id || i"
        :message="msg"
        :spoken-text-enabled="spokenTextEnabled"
        show-timestamp
        @resend="resend(i)"
      />

    </div>

    <div
      v-if="errorMessage"
      class="chat-error-row"
    >
      <p class="chat-error">
        {{ errorMessage }}
      </p>

      <button
        v-if="errorDetail"
        type="button"
        class="chat-error-details-btn"
        @click="showErrorDetail = !showErrorDetail"
      >
        {{ showErrorDetail ? 'Hide details' : 'Details' }}
      </button>
    </div>

    <pre
      v-if="errorMessage && errorDetail && showErrorDetail"
      class="chat-error-detail"
    >{{ errorDetail }}</pre>

    <p
      v-if="chatDisabled"
      class="chat-ended-notice"
    >
      {{ chatDisabledReason }}
    </p>

    <ActionButtons
      v-if="selectedSessionActive"
      :actions="state?.actions ?? []"
      :disabled="actionLoading"
      :auto-tracking-enabled="autoTrackingEnabled"
      @action="onAction"
    />

    <form
      class="input-row"
      @submit.prevent="submit"
    >
      <input
        ref="inputEl"
        v-model="draft"
        type="text"
        placeholder="Type a message..."
        :disabled="chatLoading || chatDisabled"
      />

      <button
        v-if="micAvailable"
        type="button"
        class="mic-btn"
        :class="{ 'mic-btn-recording': recording }"
        :disabled="!recording && (chatLoading || chatDisabled)"
        :title="recording ? 'Release to send' : 'Hold to record a voice message'"
        @pointerdown.prevent="startPtt"
        @pointerup="stopPtt"
        @pointerleave="stopPtt"
        @pointercancel="stopPtt"
        @contextmenu.prevent
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z" />
          <path d="M17 11a1 1 0 1 0-2 0 3 3 0 0 1-6 0 1 1 0 1 0-2 0 5 5 0 0 0 4 4.9V18H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-2.1A5 5 0 0 0 17 11z" />
        </svg>
      </button>

      <button
        v-if="talkAvailable"
        type="button"
        class="audio-btn"
        :class="{ 'audio-btn-on': audioEnabled }"
        :title="audioEnabled ? 'Audio: On' : 'Audio: Off'"
        @click="toggleAudio"
      >
        <svg v-if="audioEnabled" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M3 9v6h4l5 5V4L7 9H3z" />
          <path d="M14.5 3.23v2.06c2.89 1.2 5 4.03 5 7.71s-2.11 6.51-5 7.71v2.06c4.01-1.28 7-5.09 7-9.77s-2.99-8.49-7-9.77zM16.5 12c0-1.77-.77-3.29-2-4.34v8.68c1.23-1.05 2-2.57 2-4.34z" />
        </svg>
        <svg v-else viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M3 9v6h4l5 5V4L7 9H3z" />
          <path d="M19.73 21 21 19.73l-9-9L4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.8L19.73 21zM19 12c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89 1.2 5 4.03 5 7.71zm-4.5-2.51v.13l2.44 2.44c.04-.28.06-.56.06-.85 0-1.77-.77-3.29-2-4.34-.16-.14-.33-.27-.5-.38z" />
        </svg>
      </button>

      <button
        v-if="talkAvailable"
        type="button"
        class="spoken-text-btn"
        :class="{ 'spoken-text-btn-on': spokenTextEnabled }"
        :title="spokenTextEnabled ? 'Showing spoken text' : 'Show spoken text'"
        @click="toggleSpokenText"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
          <path d="M19 4H5c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-8 7.5H9.5v-.5h-2v2h2v-.5H11V15c0 .55-.45 1-1 1H6c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1h4c.55 0 1 .45 1 1v1.5zm7 0h-1.5v-.5h-2v2h2v-.5H18V15c0 .55-.45 1-1 1h-4c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1h4c.55 0 1 .45 1 1v1.5z" />
        </svg>
      </button>
    </form>
    </div>
  </div>
</template>

<style scoped>
.chat-window-shell {
  display: flex;
  flex-direction: row;
  flex: 1;
  min-height: 0;
  min-width: 0;
}

.chat-window {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
}

.sessions-panel-wrap {
  display: flex;
  flex-direction: row;
  min-width: 0;
  min-height: 0;
}

.panel-slide-left-enter-active,
.panel-slide-left-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.panel-slide-left-enter-from,
.panel-slide-left-leave-to {
  opacity: 0;
  transform: translateX(-16px);
}

.sessions-panel {
  display: flex;
  flex-direction: column;
  flex: none;
  min-height: 0;
  border-right: 1px solid #ddd;
  background: #f9fafb;
}

.split-divider {
  flex-shrink: 0;
  width: 6px;
  border-radius: 3px;
  background: transparent;
  cursor: col-resize;
}

.split-divider:hover {
  background: #dbe4f0;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.chat-error-row {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  padding: 0 1rem;
}

.chat-error {
  color: #c62828;
  font-size: 0.85rem;
  margin: 0;
}

.chat-error-details-btn {
  flex: none;
  border: none;
  background: none;
  color: #4a6fa5;
  font-size: 0.8rem;
  text-decoration: underline;
  cursor: pointer;
  padding: 0;
}

.chat-error-detail {
  margin: 0.3rem 1rem 0;
  padding: 0.5rem 0.75rem;
  background: #fdecea;
  color: #7a1f1f;
  font-size: 0.78rem;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-ended-notice {
  color: #444;
  background: #f5f5f7;
  margin: 0;
  padding: 0.5rem 1rem;
  font-size: 0.85rem;
}

.input-row {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-top: 1px solid #ddd;
}

.input-row input {
  flex: 1;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  font-size: 0.95rem;
}

.mic-btn,
.audio-btn,
.spoken-text-btn {
  flex: none;
  width: 2.5rem;
  border-radius: 6px;
  border: 1px solid #999;
  background: white;
  color: #666;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.mic-btn {
  touch-action: none;
  user-select: none;
}

.mic-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.mic-btn-recording {
  border-color: #c62828;
  background: #c62828;
  color: white;
  animation: mic-pulse 1.2s ease-in-out infinite;
}

.mic-btn-recording:hover {
  background: #a02020;
}

.mic-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

@keyframes mic-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(198, 40, 40, 0.5); }
  50% { box-shadow: 0 0 0 6px rgba(198, 40, 40, 0); }
}

.audio-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.audio-btn-on {
  border-color: #2e7d32;
  background: #2e7d32;
  color: white;
}

.audio-btn-on:hover:not(:disabled) {
  background: #256428;
}

.audio-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.spoken-text-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.spoken-text-btn-on {
  border-color: #2e7d32;
  background: #2e7d32;
  color: white;
}

.spoken-text-btn-on:hover:not(:disabled) {
  background: #256428;
}
</style>