<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

// A Test/draft session is a single, ephemeral conversation, so the
// embedded test chat hides the sessions panel entirely.
//
// themeMode: 'auto' (default, App.vue's own widget) shows the project's
// index.css skin the whole time, same as the live chat always has.
// 'manual' (RunChat.vue) starts unskinned instead and leaves showing it
// up to the shared applyAspect flag/toggle — owned here, not by whichever
// component happens to pass the prop, so entering/leaving manual mode is
// always symmetric: onMounted forces it off, onBeforeUnmount always
// restores it, with no separate opt-in/opt-out call for a manual-mode
// consumer to remember. applyAspect is shared across every ChatWindow
// instance (a genuine app-wide preference, see chatSkin.js), which is
// exactly why a manual instance must restore it on unmount rather than
// leaving it however it last set it.
import ActionButtons from './ActionButtons.vue'
import ChatInput from './ChatInput.vue'
import MessageBubble from './MessageBubble.vue'
import SessionsPanel from './SessionsPanel.vue'
import ProjectsMenu from '../ProjectsMenu.vue'
import SplashScreen from '../SplashScreen.vue'
import TermsView from '../TermsView.vue'
import { setApiError } from '../../errorStore.js'
import { startRecording, stopRecording } from '../../mic.js'
import { projectFileContentUrl } from '../../api.js'
import { liveStore } from '../../chatStore.js'
import {
  audioEnabled,
  talkAvailable,
  micAvailable,
  spokenTextEnabled,
  toggleSpokenText,
} from '../../chatStoreFactory.js'
import { applyAspect } from '../../chatSkin.js'

const props = defineProps({
  hideSessionsPanel: { type: Boolean, default: false },
  themeMode: { type: String, default: 'auto' },
  // Which chat conversation this window renders — its own independent
  // session/messages/state, never shared with any other store instance
  // (see chatStoreFactory.js's createChatStore). Defaults to the app's
  // one live chat; RunChat.vue passes its own test store instead.
  store: { type: Object, default: () => liveStore }
})

const {
  state,
  messages,
  chatLoading,
  chatStatus,
  actionLoading,
  autoTrackingEnabled,
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
  handleReact,
  handleVoiceMessage,
  handleAction,
  toggleAudio,
  projectPaused,
  projectPausedReason,
  currentProjectName,
  legalTermsPending,
  acceptLegalTerms
} = props.store

const emit = defineEmits(['project-select', 'project-download'])

const projectsMenuRef = ref(null)

// TermsView.vue's own fetchTerms override — same {content} shape as
// getTerms(), just reading this project's own legal/terms.md pinned to
// the live session that triggered legalTermsPending in the first place
// (see chatSkin.js's loadSkin for the identical fetch/credentials pattern,
// used there for index.css instead).
async function fetchLegalTerms() {
  const response = await fetch(
    projectFileContentUrl(currentProjectName.value, 'legal/terms.md', currentSessionId.value),
    { credentials: 'include', cache: 'no-store' }
  )
  if (!response.ok) throw new Error('Failed to load this application’s terms.')
  return { content: await response.text() }
}

defineExpose({
  refreshProjectsMenu: () => projectsMenuRef.value?.refresh()
})

// No transcript import here: imported sessions are a separate pool that
// never shows up in this component's own sessions list, so importing
// from here would silently succeed and then vanish from view.

function createSession() {
  handleNewSession()
}

// The sessions panel overlays the chat rather than sharing space with it
// (see .sessions-panel-wrap), so any click into the chat pane behind it
// reads as "dismiss the panel" — same as tapping outside a drawer/sheet.
function closeSessionsPanelOnChatClick() {
  if (!props.hideSessionsPanel && sessionsPanelOpen.value) toggleSessionsPanel()
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

// Auto-collapses the sessions panel after 5s of no interaction inside it —
// reset on every mousemove/click/keydown/scroll there (see the template's
// own listeners on .sessions-panel-wrap), started/stopped by the watch
// below whenever sessionsPanelOpen itself changes.
const SESSIONS_PANEL_AUTO_COLLAPSE_MS = 5000
let sessionsPanelIdleTimer = null

function clearSessionsPanelIdleTimer() {
  if (sessionsPanelIdleTimer) {
    clearTimeout(sessionsPanelIdleTimer)
    sessionsPanelIdleTimer = null
  }
}

function resetSessionsPanelIdleTimer() {
  clearSessionsPanelIdleTimer()
  if (props.hideSessionsPanel || !sessionsPanelOpen.value) return
  sessionsPanelIdleTimer = setTimeout(() => {
    if (sessionsPanelOpen.value) toggleSessionsPanel()
  }, SESSIONS_PANEL_AUTO_COLLAPSE_MS)
}

watch(sessionsPanelOpen, (open) => {
  if (open) resetSessionsPanelIdleTimer()
  else clearSessionsPanelIdleTimer()
})

onMounted(() => {
  window.addEventListener('mousemove', onSessionsDrag)
  window.addEventListener('mouseup', stopSessionsDrag)
  if (props.themeMode === 'manual') applyAspect.value = false
  if (sessionsPanelOpen.value) resetSessionsPanelIdleTimer()
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onSessionsDrag)
  window.removeEventListener('mouseup', stopSessionsDrag)
  if (props.themeMode === 'manual') applyAspect.value = true
  clearSessionsPanelIdleTimer()
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
  <!-- Fixed/full-viewport (see TermsView.vue's own styling), so its
       placement here doesn't matter — it covers the sessions panel,
       header, and footer alike regardless of where in this template it
       sits. Only ever true for the live store (see chatStoreFactory.js's
       legalTermsPending): this project's own legal/terms.md changed since
       the user's previous live session here, or this is their first one. -->
  <TermsView
    v-if="legalTermsPending"
    :show-reject="false"
    :fetch-terms="fetchLegalTerms"
    @accept="acceptLegalTerms"
  />

  <div
    class="chat-window-shell"
    :class="state?.key ? `state-${state.key}` : null"
    :data-state="state?.key ?? null"
    :data-prev-state="prevStateKey"
  >
    <Transition name="sessions-slide">
      <div
        v-if="!hideSessionsPanel && sessionsPanelOpen"
        class="sessions-panel-wrap"
        @mousemove="resetSessionsPanelIdleTimer"
        @click="resetSessionsPanelIdleTimer"
        @keydown="resetSessionsPanelIdleTimer"
        @scroll.capture="resetSessionsPanelIdleTimer"
      >
        <div class="sessions-panel" :style="{ width: sessionsWidth + 'px' }">
          <div class="sessions-panel-project-menu">
            <ProjectsMenu
              ref="projectsMenuRef"
              @select="(name) => emit('project-select', name)"
              @download="(name) => emit('project-download', name)"
            />
            <button
              class="sessions-panel-close-btn"
              title="Collapse sessions"
              @click="toggleSessionsPanel"
            >✕</button>
          </div>
          <SessionsPanel
            :sessions="sessions"
            :loading="sessionsLoading"
            :current-session-id="currentSessionId"
            :deleting-session-id="deletingSessionId"
            :create-disabled="!state?.key"
            hide-collapse-toggle
            restrict-selection-to-native
            @select="selectSession"
            @create="createSession"
            @delete="onDeleteSession"
          />
        </div>

        <div class="split-divider" @mousedown="startSessionsDrag"></div>
      </div>
    </Transition>

    <div
      class="chat-window"
      :class="{ 'chat-window-dimmed': !hideSessionsPanel && sessionsPanelOpen }"
      @click="closeSessionsPanelOnChatClick"
    >
    <button
      v-if="!hideSessionsPanel && !sessionsPanelOpen"
      type="button"
      class="sessions-reopen-btn"
      title="Show sessions"
      @click.stop="toggleSessionsPanel"
    >☰</button>

    <SplashScreen v-if="!hideSessionsPanel && projectPaused" variant="paused" :reason="projectPausedReason" embedded />
    <SplashScreen v-else-if="!hideSessionsPanel && !state?.key" variant="no-project" embedded />
    <template v-else>
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
          :reactions="state?.reactions || []"
          show-timestamp
          @resend="resend(i)"
          @react="handleReact(msg.messageId, $event)"
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
    </template>
    </div>
  </div>
</template>

<style scoped>
.chat-window-shell {
  position: relative;
  display: flex;
  flex-direction: row;
  flex: 1;
  min-height: 0;
  min-width: 0;
}

.chat-window {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
  /* Leaving (dimmed class removed) targets this rule, so un-blurring is
     instant — no animation back to full focus. */
  transition: filter 0s;
  will-change: filter;
  /* Promotes the whole pane to its own compositing layer so the browser
     doesn't repaint the entire message list (markdown, images, code
     blocks) on every frame of the blur transition — without this the
     animation drops frames on anything but a trivial conversation. */
  transform: translateZ(0);
  backface-visibility: hidden;
}

.chat-window-dimmed {
  filter: blur(2.5px);
  /* Entering targets this rule instead of the base one above: waits for
     the sessions panel's own 0.32s slide-in to finish, then the blur
     snaps on instantly (0s duration) rather than fading in — no animated
     blur in either direction, only the panel/overlay-icon movement is. */
  transition: filter 0s 0.32s;
}

.sessions-reopen-btn {
  position: absolute;
  top: 0.75rem;
  left: 0.75rem;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  line-height: 1;
  border: 1px solid #4a6fa5;
  border-radius: 6px;
  background: #fff;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.9rem;
  opacity: 0.35;
  transition: opacity 0.15s ease;
}

.sessions-reopen-btn:hover {
  opacity: 1;
}

/* Overlays the chat rather than sitting in the flex flow — opening it
   must never resize/reflow the chat pane underneath. z-index above
   App.vue's .topbar-overlay (30): open, this panel must cover those
   fixed buttons, never sit under them (though .topbar-overlay-hidden
   already fades them out in lockstep with this opening anyway). */
.sessions-panel-wrap {
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
  z-index: 35;
  display: flex;
  flex-direction: row;
  min-width: 0;
  min-height: 0;
}

.sessions-slide-enter-active,
.sessions-slide-leave-active {
  transition: transform 0.32s cubic-bezier(0.22, 1, 0.36, 1);
}

.sessions-slide-enter-from,
.sessions-slide-leave-to {
  transform: translateX(-100%);
}

.sessions-panel {
  display: flex;
  flex-direction: column;
  flex: none;
  min-height: 0;
  border-right: 1px solid #ddd;
  background: #f9fafb;
  box-shadow: 1px 0 3px rgba(0, 0, 0, 0.55), 6px 0 24px rgba(0, 0, 0, 0.35);
}

.sessions-panel-project-menu {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.6rem 0.9rem;
  border-bottom: 1px solid #ddd;
}

.sessions-panel-project-menu .projects-menu {
  flex: 1;
  min-width: 0;
}

.sessions-panel-close-btn {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #666;
  cursor: pointer;
  font-size: 0.9rem;
}

.sessions-panel-close-btn:hover {
  background: #eee;
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
  height: 70px;
}

.chat-footer {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

/* While the sessions panel is open, the chat behind it is inert — dimmed
   and non-interactive, so a click anywhere just reaches
   closeSessionsPanelOnChatClick above instead of the chat's own controls. */
.chat-window-dimmed .chat-header,
.chat-window-dimmed .messages,
.chat-window-dimmed .chat-ended-notice,
.chat-window-dimmed .chat-footer {
  pointer-events: none;
}

.chat-window::after {
  content: '';
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.14);
  z-index: 15;
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.32s cubic-bezier(0.22, 1, 0.36, 1);
}

.chat-window-dimmed::after {
  opacity: 1;
}
</style>