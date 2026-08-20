<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ActionButtons from './ActionButtons.vue'
import ChatInput from './ChatInput.vue'
import MessageBubble from './MessageBubble.vue'
import SessionsPanel from './SessionsPanel.vue'
import { setApiError } from '../../errorStore.js'
import { startRecording, stopRecording } from '../../mic.js'
import { projectFileContentUrl } from '../../api.js'
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
  currentProjectName,
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

// No transcript import here: this window is either the main app's live
// chat, or EditProjectView.vue's own embedded "Test" chat (see
// chatStore.js's testModeProjectName) — an imported session is a
// separate, 'imported'-source pool of its own (see backend db.py's
// ChatSession.source) that never shows up in either one's own sessions
// list (list_sessions/list_test_sessions both filter it out), so
// importing from here would silently succeed server-side and then just
// vanish from view. Only LabelProjectView.vue's own dedicated
// review panel — which actually lists imported sessions — offers import
// (it renders SessionsPanel.vue directly, with its own allow-import,
// rather than going through this component at all).

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

// Tracks the state key just left, for the data-prev-state attribute below
// — set once on the first real transition and never cleared back out
// afterward (a caller styling a "leaving state X" transition still wants
// to know X even once the transition itself is long over).
const prevStateKey = ref(null)
watch(
  () => state.value?.key,
  (newKey, oldKey) => {
    if (oldKey != null) prevStateKey.value = oldKey
  }
)

// index.css, injected as a plain <style> element appended to <head> (so it
// lands after every component's own scoped styles, and can override them)
// — the custom "skin" a project's own draft/published index.css defines
// for its chat UI. Resolved against currentSessionId's own revision (see
// api.js's projectFileContentUrl/controller.py's get_project_file_content):
// a live session sees whatever was published when it started, a Test
// session (EditProjectView.vue's embedded chat) always sees the current
// draft — same distinction the automaton itself already makes for that
// session, no special-casing needed here. A project with no index.css at
// all 404s silently: no stylesheet, not an error.
let skinStyleEl = null

function clearSkin() {
  skinStyleEl?.remove()
  skinStyleEl = null
}

async function loadSkin() {
  const projectName = currentProjectName.value
  const sessionId = currentSessionId.value
  if (!projectName || sessionId == null) {
    clearSkin()
    return
  }
  let css
  try {
    const response = await fetch(projectFileContentUrl(projectName, 'index.css', sessionId))
    if (!response.ok) {
      clearSkin()
      return
    }
    css = await response.text()
  } catch {
    return
  }
  if (!skinStyleEl) {
    skinStyleEl = document.createElement('style')
    document.head.appendChild(skinStyleEl)
  }
  skinStyleEl.textContent = css
}

watch([currentProjectName, currentSessionId], loadSkin, { immediate: true })
onBeforeUnmount(clearSkin)
</script>

<template>
  <div
    class="chat-window-shell"
    :class="state?.key ? `state-${state.key}` : null"
    :data-state="state?.key ?? null"
    :data-prev-state="prevStateKey"
  >
    <div class="sessions-panel-wrap">
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
    <div class="chat-header"></div>

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
      v-if="chatDisabled"
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

/* Empty of functional content on its own — a pure style hook (see this
   component's own docstring) so a project's own index.css can target
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