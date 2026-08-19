<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ActionButtons from './ActionButtons.vue'
import ChatInput from './ChatInput.vue'
import MessageBubble from './MessageBubble.vue'
import SessionsPanel from './SessionsPanel.vue'
import { setApiError } from '../../errorStore.js'
import { startRecording, stopRecording } from '../../mic.js'
import { postImportSession } from '../../api.js'
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
  toggleSessionsPanel,
  selectSession,
  handleNewSession,
  handleDeleteSession,
  handleSend,
  handleResend,
  handleVoiceMessage,
  handleAction,
  toggleAudio,
  toggleSpokenText,
  refreshSessionsQuietly
} from '../../chatStore.js'

// Only the "Edit project" view's own embedded chat passes either of
// these — a transcript import is meaningful there (reviewing/testing a
// project still being authored), not for the main app's live chat
// window, which stays at its current default (see App.vue's own usage,
// unchanged). projectName: see chatStore.js's own handleNewSession/
// loadMessages — only EditProjectView.vue's own "Test" mode passes this
// (its own projectName), the one place a session is allowed to start
// against a revision nobody's published yet; every other caller leaves
// it null, same behavior as always.
const props = defineProps({
  allowImport: { type: Boolean, default: false },
  projectName: { type: String, default: null }
})

function createSession() {
  handleNewSession(props.projectName)
}

async function handleImportSession(file) {
  try {
    await postImportSession(file)
    await refreshSessionsQuietly(true)
  } catch {
    // already surfaced via apiFetch
  }
}

const scrollEl = ref(null)
const chatInputRef = ref(null)
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

// Mirrors chatDisabled's own three conditions — no project/state at all
// is checked first (the most fundamental one), then whichever reason
// selectedSessionActive is false (see its own docstring in chatStore.js:
// "never resolved a session yet" — currentSessionId still null — reads
// differently than "resolved one, then it got superseded by a newer
// one"), then a chat-blocked state (e.g. final, see backend
// chat_service.py's own "doesn't accept messages" wording).
const chatDisabledReason = computed(() => {
  if (!state.value?.key) return 'Please select a project from the menu.'
  if (!selectedSessionActive.value) {
    return currentSessionId.value == null
      ? 'No active session for this project yet.'
      : 'This session is no longer active.'
  }
  return "This state doesn't accept messages; use an action instead."
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
  if (event?.pointerType === 'mouse' && event.button !== 0) return
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
  chatInputRef.value?.focus()
})

function focusInput() {
  if (chatDisabled.value) return
  chatInputRef.value?.focus()
}

async function onAction(actionName) {
  await handleAction(actionName)
  await nextTick()
  focusInput()
}
</script>

<template>
  <div class="chat-window-shell">
    <div class="sessions-panel-wrap">
      <div class="sessions-panel" :class="{ 'sessions-panel-collapsed': !sessionsPanelOpen }" :style="sessionsPanelOpen ? { width: sessionsWidth + 'px' } : null">
        <SessionsPanel
          :sessions="sessions"
          :loading="sessionsLoading"
          :current-session-id="currentSessionId"
          :deleting-session-id="deletingSessionId"
          :allow-import="allowImport"
          :collapsed="!sessionsPanelOpen"
          restrict-selection-to-native
          @update:collapsed="toggleSessionsPanel"
          @select="selectSession"
          @create="createSession"
          @delete="onDeleteSession"
          @import="handleImportSession"
        />
      </div>

      <div v-if="sessionsPanelOpen" class="split-divider" @mousedown="startSessionsDrag"></div>
    </div>

    <div class="chat-window">
    <div class="messages" ref="scrollEl">
      <slot name="timeline">
        <MessageBubble
          v-for="(msg, i) in messages"
          :key="msg.messageId || msg.id || i"
          :message="msg"
          :spoken-text-enabled="spokenTextEnabled"
          show-timestamp
          @resend="resend(i)"
        />
      </slot>
    </div>

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

    <ChatInput
      ref="chatInputRef"
      v-model="draft"
      :disabled="chatLoading || chatDisabled"
      :recording="recording"
      :mic-available="micAvailable"
      :talk-available="talkAvailable"
      :audio-enabled="audioEnabled"
      :spoken-text-enabled="spokenTextEnabled"
      @submit="submit"
      @mic-start="startPtt"
      @mic-stop="stopPtt"
      @toggle-audio="toggleAudio"
      @toggle-spoken-text="toggleSpokenText"
    />
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

.sessions-panel {
  display: flex;
  flex-direction: column;
  flex: none;
  min-height: 0;
  border-right: 1px solid #ddd;
  background: #f9fafb;
  transition: width 0.15s ease;
}

/* Collapsed (see SessionsPanel.vue's own always-visible header toggle) —
   a slim strip, same pattern as EditProjectView.vue's own
   .inspector-panel-collapsed. */
.sessions-panel-collapsed {
  width: 2.4rem !important;
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

.chat-ended-notice {
  color: #444;
  background: #f5f5f7;
  margin: 0;
  padding: 0.5rem 1rem;
  font-size: 0.85rem;
}
</style>