<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

// A Test/draft session is a single, ephemeral conversation, so the
// embedded test chat hides the sessions panel entirely.
const props = defineProps({
  hideSessionsPanel: { type: Boolean, default: false }
})
import ActionButtons from './ActionButtons.vue'
import ChatInput from './ChatInput.vue'
import MessageBubble from './MessageBubble.vue'
import SessionsPanel from './SessionsPanel.vue'
import { setApiError } from '../../errorStore.js'
import { startRecording, stopRecording } from '../../mic.js'
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
  toggleSpokenText
} from '../../chatStore.js'

// No transcript import here: imported sessions are a separate pool that
// never shows up in this component's own sessions list, so importing
// from here would silently succeed and then vanish from view.

function createSession() {
  handleNewSession()
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

// selectedSessionActive reflects the backend's "active" verdict for the
// displayed session, never recomputed here from a timestamp — only the
// most recently started open session per project is ever active.
const chatDisabled = computed(() => !state.value?.key || !state.value?.chat || !selectedSessionActive.value)

// Mirrors chatDisabled's own conditions, in the same order. A state with
// no chat has nothing generic to say here — it may have no actions
// either, so pointing at "use an action instead" would be wrong as often
// as not; the input stays disabled with no explanation for that case.
const chatDisabledReason = computed(() => {
  if (!state.value?.key) return 'Please select a project from the menu.'
  if (!selectedSessionActive.value) {
    return currentSessionId.value == null
      ? 'No active session for this project yet.'
      : 'This session is no longer active.'
  }
  return null
})

// Draggable divider between the sessions panel and the chat itself.
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

// Auto-scroll when new messages arrive or stream in.
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

// Tracks the state key just left, for the data-prev-state attribute below.
// Set once per transition and never cleared, so it stays available for
// styling a "leaving state X" transition after the fact.
const prevStateKey = ref(null)
watch(
  () => state.value?.key,
  (newKey, oldKey) => {
    if (oldKey != null) prevStateKey.value = oldKey
  }
)

// index.css's "skin" is applied globally now (see chatStore.js's own
// loadSkin) — one shared <style> for the whole app rather than one per
// ChatWindow instance, since App.vue's own widget stays mounted behind
// EditProjectView's overlay the entire time it's open and would
// otherwise fight this instance's tag for which one's rules actually win.
</script>

<template>
  <div
    class="chat-window-shell"
    :class="state?.key ? `state-${state.key}` : null"
    :data-state="state?.key ?? null"
    :data-prev-state="prevStateKey"
  >
    <div v-if="!hideSessionsPanel" class="sessions-panel-wrap">
      <div class="sessions-panel" :class="{ 'sessions-panel-collapsed': !sessionsPanelOpen }" :style="sessionsPanelOpen ? { width: sessionsWidth + 'px' } : null">
        <SessionsPanel
          :sessions="sessions"
          :loading="sessionsLoading"
          :current-session-id="currentSessionId"
          :deleting-session-id="deletingSessionId"
          :collapsed="!sessionsPanelOpen"
          restrict-selection-to-native
          @update:collapsed="toggleSessionsPanel"
          @select="selectSession"
          @create="createSession"
          @delete="onDeleteSession"
        />
      </div>

      <div v-if="sessionsPanelOpen" class="split-divider" @mousedown="startSessionsDrag"></div>
    </div>

    <div class="chat-window">
    <div class="chat-header">
      <div class="chat-header-icon"></div>
    </div>

    <div class="messages chat-body" ref="scrollEl">
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
      v-if="chatDisabledReason"
      class="chat-ended-notice"
    >
      {{ chatDisabledReason }}
    </p>

    <div class="chat-footer">
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

/* Collapsed to a slim strip; the header toggle stays visible. */
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

/* Empty on its own: a style hook so a project's index.css can target
   .chat-header/.chat-body/.chat-footer without reaching into internals. */
.chat-header {
  flex-shrink: 0;
}

.chat-footer {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
</style>