<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

// A Test/draft session is a single, ephemeral conversation, so the
// embedded test chat hides the sessions panel entirely.
//
// themeMode: 'auto' (default, LiveChatWindow.vue's own instance) shows
// the project's index.css skin the whole time, same as the live chat
// always has. 'manual' (RunChat.vue) leaves showing it up to the shared
// applyAspect flag/toggle — owned here, not by whichever component happens
// to pass the prop, so entering/leaving manual mode is always symmetric:
// onMounted applies manualApplyAspectPreference (defaults to unskinned on
// first-ever use), onBeforeUnmount saves whatever the toggle ended up at
// back into that preference and always restores applyAspect itself to
// true. applyAspect is shared across every ChatView instance (a genuine
// app-wide preference, see chatSkin.js), which is exactly why a manual
// instance must restore it on unmount rather than leaving it however it
// last set it — manualApplyAspectPreference is what lets a manual
// instance still remember its own choice across that reset.
import ActionButtons from './ActionButtons.vue'
import ChatInput from './ChatInput.vue'
import MessageBubble from './MessageBubble.vue'
import SessionsPanel from './SessionsPanel.vue'
import ProjectsMenu from '../ProjectsMenu.vue'
import ProfileMenu from '../ProfileMenu.vue'
import AppHeader from '../AppHeader.vue'
import SplashScreen from '../SplashScreen.vue'
import { roleSatisfies } from '../../roles.js'
import { setApiError } from '../../errorStore.js'
import { connect as connectChat, disconnect as disconnectChat } from '../../chatClient.js'
import { startRecording, stopRecording } from '../../mic.js'
import { unlockAudioPlayback } from '../../audio.js'
import { liveStore } from '../../chatStore.js'
import {
  audioEnabled,
  talkAvailable,
  micAvailable,
  spokenTextEnabled,
  toggleSpokenText,
} from '../../chatStoreFactory.js'
import { applyAspect, manualApplyAspectPreference } from '../../chatSkin.js'

const props = defineProps({
  hideSessionsPanel: { type: Boolean, default: false },
  themeMode: { type: String, default: 'auto' },
  // Which chat conversation this window renders — its own independent
  // session/messages/state, never shared with any other store instance
  // (see chatStoreFactory.js's createChatStore). Defaults to the app's
  // one live chat; RunChat.vue passes its own test store instead.
  store: { type: Object, default: () => liveStore },
  // Only ever set by LiveChatWindow.vue's real, top-level instance (see
  // hideSessionsPanel above — RunChat.vue's embedded preview leaves both
  // unset) — gates the header's own back-to-Manage-projects button below.
  role: { type: String, default: null },
  // ProfileMenu.vue's own avatar/name, same pass-through every other
  // top-level view already does.
  profile: { type: Object, default: null }
})

const {
  state,
  messages,
  chatLoading,
  chatStatus,
  actionLoading,
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
  reloadMessages
} = props.store

const emit = defineEmits(['project-select', 'project-download', 'manage-projects', 'profile', 'logout'])

const projectsMenuRef = ref(null)

// The header's own back arrow — only an admin (whose LiveChatWindow is
// pushed *over* ManageProjectsView, see App.vue) has anywhere to pop back
// to; a plain user's chat is their whole app, with no base to return to.
const canBackToManageProjects = computed(() => roleSatisfies(props.role, 'admin'))

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

defineExpose({
  refreshProjectsMenu: () => projectsMenuRef.value?.refresh(),
  focus: () => chatInputRef.value?.focus()
})

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

// Swipe-left-to-close on touch — the divider drag above is mouse-only
// (mousedown/mousemove), and on a full-width mobile drawer (see
// .sessions-panel's own mobile width below) there's no divider to grab
// anyway. Tracks whether the gesture reads as more horizontal than
// vertical before committing, so it doesn't hijack a vertical scroll
// inside the panel's own session list.
const SWIPE_CLOSE_THRESHOLD_PX = 60
let swipeStartX = 0
let swipeStartY = 0
let swipeTracking = false

function onSessionsPanelTouchStart(event) {
  const touch = event.touches[0]
  if (!touch) return
  swipeStartX = touch.clientX
  swipeStartY = touch.clientY
  swipeTracking = true
}

function onSessionsPanelTouchMove(event) {
  if (!swipeTracking) return
  const touch = event.touches[0]
  if (!touch) return
  const dx = touch.clientX - swipeStartX
  const dy = touch.clientY - swipeStartY
  if (Math.abs(dx) < SWIPE_CLOSE_THRESHOLD_PX || Math.abs(dx) < Math.abs(dy)) return
  swipeTracking = false
  if (dx < 0) toggleSessionsPanel()
}

function onSessionsPanelTouchEnd() {
  swipeTracking = false
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

// Mobile backgrounds the page constantly (app switch, screen lock) — iOS
// suspends the webview and drops the socket within seconds of that, and
// nothing was listening for the return trip before this. connect()/
// disconnect() are chatClient.js's own public API (that file itself is
// off-limits to edit) — reconnecting is a plain close-then-reopen, same
// as a fresh mount does today. This can't repair one specific case: a
// transient failure sets chatClient.js's own websocketUnavailable latch,
// which has no exposed reset, so connect() silently no-ops for the rest
// of the page's life after that — fixing that needs a small change
// inside chatClient.js, which needs sign-off first rather than a
// workaround built around it from out here.
function onVisibilityChange() {
  if (document.visibilityState !== 'visible') return
  disconnectChat()
  connectChat()
  reloadMessages?.()
}

onMounted(() => {
  connectChat()
  window.addEventListener('mousemove', onSessionsDrag)
  window.addEventListener('mouseup', stopSessionsDrag)
  document.addEventListener('visibilitychange', onVisibilityChange)
  if (props.themeMode === 'manual') applyAspect.value = manualApplyAspectPreference.value
  if (sessionsPanelOpen.value) resetSessionsPanelIdleTimer()
})
onBeforeUnmount(() => {
  disconnectChat()
  window.removeEventListener('mousemove', onSessionsDrag)
  window.removeEventListener('mouseup', stopSessionsDrag)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  if (props.themeMode === 'manual') {
    manualApplyAspectPreference.value = applyAspect.value
    applyAspect.value = true
  }
  clearSessionsPanelIdleTimer()
})

function submit() {
  const text = draft.value.trim()
  if (!text || chatLoading.value || chatDisabled.value) return
  // Inside this same click/submit gesture — narration for the reply this
  // triggers plays moments later, well outside any gesture of its own.
  unlockAudioPlayback()
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
  // Inside this same pointerdown gesture — the voice message this
  // eventually sends gets a reply whose own narration plays well outside
  // any gesture of its own.
  unlockAudioPlayback()
  // getUserMedia only exists in a secure context (https, or localhost) —
  // over plain http on a LAN it's simply undefined, which otherwise
  // surfaces as the same "access was denied" message a real permission
  // refusal gives, hiding the actual (unfixable-by-the-user) cause.
  if (!navigator.mediaDevices?.getUserMedia) {
    setApiError(
      'Microphone unavailable.',
      window.isSecureContext ? undefined : 'This page must be loaded over https to use the microphone.'
    )
    return
  }
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

// Whether the transcript was already scrolled near its bottom before the
// latest change — read before each new-content auto-scroll below so
// streaming/new messages don't yank someone back down mid-reread further
// up. Starts true (a fresh mount/session has nothing to scroll away
// from yet) and resets to true on session switch, since that view always
// opens pinned at the bottom regardless of where the previous one sat.
const NEAR_BOTTOM_THRESHOLD_PX = 80
const userNearBottom = ref(true)

function onMessagesScroll() {
  const el = scrollEl.value
  if (!el) return
  userNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_THRESHOLD_PX
}

watch(currentSessionId, () => { userNearBottom.value = true })

// Auto-scroll when new messages arrive or stream in — but only for
// someone already reading the live edge, not someone scrolled back
// through history (see userNearBottom above).
watch(
  messages,
  () => {
    if (userNearBottom.value) scrollToBottom()
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
// ChatView instance, since LiveChatWindow.vue's own instance stays
// mounted behind EditProjectView's overlay the entire time it's open and
// would otherwise fight this instance's tag for which one's rules
// actually win.
</script>

<template>
  <div class="chat-window-outer">
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
        @touchstart.passive="resetSessionsPanelIdleTimer"
        @touchmove.passive="resetSessionsPanelIdleTimer"
      >
        <div
          class="sessions-panel"
          :style="{ width: sessionsWidth + 'px' }"
          @touchstart.passive="onSessionsPanelTouchStart"
          @touchmove.passive="onSessionsPanelTouchMove"
          @touchend.passive="onSessionsPanelTouchEnd"
        >
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
    <AppHeader
      v-if="!hideSessionsPanel"
      variant="overlay"
      class="chat-header-overlay"
      :class="{ 'chat-header-overlay-hidden': sessionsPanelOpen }"
    >
      <template #left>
        <button
          v-if="!sessionsPanelOpen"
          type="button"
          class="app-header-icon-btn"
          title="Show sessions"
          @click.stop="toggleSessionsPanel"
        >☰</button>
        <button
          v-if="canBackToManageProjects"
          type="button"
          class="app-header-icon-btn"
          title="Back to Manage projects"
          @click="emit('manage-projects')"
        >«</button>
      </template>
      <template #right>
        <ProfileMenu :profile="profile" @profile="emit('profile')" @logout="emit('logout')" />
      </template>
    </AppHeader>

    <SplashScreen v-if="!hideSessionsPanel && projectPaused" variant="paused" :reason="projectPausedReason" embedded />
    <SplashScreen v-else-if="!hideSessionsPanel && !state?.key" variant="no-project" embedded />
    <template v-else>
    <div class="chat-header">
      <div class="chat-header-icon"></div>
    </div>

    <div class="messages chat-body" ref="scrollEl" @scroll="onMessagesScroll">
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
        :actions="state?.manual_actions ?? []"
        :disabled="actionLoading"
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
  </div>
</template>

<style scoped>
.chat-window-outer {
  display: flex;
  flex: 1;
  min-height: 0;
  min-width: 0;
  background: white;
}

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

/* Fades out in lockstep with the sessions panel sliding open — same
   reasoning as ChatWindow.vue's own .chat-window-dimmed (see below): once
   the panel covers this corner, its own controls take over. */
.chat-header-overlay {
  transition: opacity 0.32s cubic-bezier(0.22, 1, 0.36, 1);
}

.chat-header-overlay-hidden {
  opacity: 0;
  pointer-events: none;
}

/* Overlays the chat rather than sitting in the flex flow — opening it
   must never resize/reflow the chat pane underneath. z-index above
   LiveChatWindow.vue's own .topbar-overlay (30): open, this panel must
   cover those fixed buttons, never sit under them (though
   .topbar-overlay-hidden already fades them out in lockstep with this
   opening anyway). */
.sessions-panel-wrap {
  position: absolute;
  /* Not top: 0 — this overlay sits inside .chat-window, a *sibling* of
     .chat-header (the element that actually reserves the notch, see its
     own comment), not a descendant of it, so it never inherited that
     padding: it invaded the notch itself, right where its own project
     menu/close button became unreachable. Same for the bottom edge and
     the home indicator. */
  top: var(--safe-area-top);
  left: 0;
  bottom: var(--safe-area-bottom);
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

/* A fixed 240px drawer left an 80px sliver of chat visible beside it on
   a 320px phone — full-width sheet instead, same convention as a mobile
   nav drawer. The resize divider (mousedown/mousemove only, inert on
   touch anyway) has nothing to do at full width, so it's hidden too. */
@media (max-width: 640px) {
  /* .sessions-panel-wrap otherwise has no explicit width (only
     top/left/bottom) — shrink-to-fit around its content, which left
     .sessions-panel's own 100% below resolving against that same
     shrink-to-fit content width instead of the viewport. right: 0 gives
     it a real, viewport-wide box for that percentage to resolve against. */
  .sessions-panel-wrap {
    right: 0;
  }

  .sessions-panel {
    width: 100% !important;
  }

  .split-divider {
    display: none;
  }
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  /* Without this, an over-scroll past the top on Android Chrome falls
     through to the browser's own pull-to-refresh — a full SPA reload,
     losing the draft and re-running the terms/session checks. */
  overscroll-behavior-y: contain;
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
  position: relative;
  /* Reserves the notch/status bar — content-box on purpose (not
     border-box): this should *add* to the 70px a project's skin already
     sizes its own icon/content against, not eat into it. Living here
     rather than on LiveChatWindow.vue's own wrapper means this
     element's own background — the one a skin actually paints (see the
     .chat-header hook in that file's comment) — extends behind the
     notch instead of leaving a color-mismatched gap above it.
     Left/right aren't reserved here too — LiveChatWindow.vue's own
     .live-chat-window already does, and reserving both would double it. */
  padding-top: var(--safe-area-top);
}

.chat-footer {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  /* Same reasoning as .chat-header above, for the opposite edge — the
     home indicator (iOS) / gesture nav bar (Android). ChatInput.vue's
     own .input-row no longer reserves this itself (see its own
     comment), so this is the only place it's reserved now. */
  padding-bottom: var(--safe-area-bottom);
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
