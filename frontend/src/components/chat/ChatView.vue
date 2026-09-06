<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

// A Test/draft session is a single, ephemeral conversation with no
// project to switch away from and no paused/no-project splash screen of
// its own, so the embedded test chat hides this header entirely.
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
import ProjectsMenu from '../ProjectsMenu.vue'
import ProfileMenu from '../ProfileMenu.vue'
import AppHeader from '../AppHeader.vue'
import SplashScreen from '../SplashScreen.vue'
import { setApiError } from '../../errorStore.js'
import { startRecording, stopRecording } from '../../mic.js'
import { unlockAudioPlayback } from '../../audio.js'
import { liveStore } from '../../chatStore.js'
import {
  audioEnabled,
  talkAvailable,
  micAvailable,
  spokenTextEnabled,
  toggleSpokenText,
  chatConnectionState,
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
  handleNewSession,
  handleCloseSession,
  handleSend,
  handleResend,
  handleReact,
  handleVoiceMessage,
  handleAction,
  toggleAudio,
  projectPaused,
  projectPausedReason,
  reloadMessages,
  humanTalkerEnabled,
  humanTalkerLoading,
  toggleHumanTalker
} = props.store

const emit = defineEmits(['project-select', 'project-download', 'manage-projects', 'home', 'profile', 'logout'])

const projectsMenuRef = ref(null)

// The header's own back arrow — only an admin (pushed *over*
// ManageProjectsView) or a customer (pushed *over* AppStoreView, see
// App.vue) has anywhere to pop back to; a plain user's chat is their whole
// app, with no base to return to.
const canBackToManageProjects = computed(() => props.role === 'admin' || props.role === 'customer')
const backLabel = computed(() => props.role === 'customer' ? 'Back to App store' : 'Back to Manage projects')

// Manual-testing toggle for HumanTalker (see talker.human_talker,
// chatStoreFactory.js's own toggleHumanTalker) — admin-only (matches
// MAX_CONNECTIONS_PER_ADMIN, the two-tab setup this is built to test) and
// only once there's an actual session to toggle it on.
const showHumanTalkerToggle = computed(() => props.role === 'admin' && currentSessionId.value != null)

const scrollEl = ref(null)
const chatInputRef = ref(null)
const recording = ref(false)

defineExpose({
  refreshProjectsMenu: () => projectsMenuRef.value?.refresh(),
  focus: () => chatInputRef.value?.focus()
})

// selectedSessionActive reflects the backend's "active" verdict for the
// displayed session, never recomputed here from a timestamp — only the
// most recently started open session per project is ever active.
// The websocket is the chat's only transport (see chatClient.js): with no
// connection there is nowhere to send a message, so the input closes until
// it is back — the one and only reason it closes besides the session
// itself being unusable.
const chatConnected = computed(() => chatConnectionState.value === 'open')

// 'rejected' (see chatChannel.js's ALREADY_CONNECTED_CLOSE_CODE handling):
// this identity is already at its per-role connection cap on another tab —
// unlike the generic disconnected state below, this one will never resolve
// on its own by retrying, so it gets its own, non-"riprovo" message.
const chatRejected = computed(() => chatConnectionState.value === 'rejected')

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

// Mobile backgrounds the page constantly (app switch, screen lock) — iOS
// suspends the webview and drops the socket within seconds of that.
// Reopening the socket is not this view's business: chatClient.js watches
// visibility itself and reconnects, in one place, for the whole app (and
// resynchronizes this session when it does). All that is left here is the
// plain history refresh for a tab that was away while nothing was in
// flight.
function onVisibilityChange() {
  if (document.visibilityState !== 'visible') return
  // A reload mid-turn would replace `messages` out from under the
  // in-flight assistant bubble submitMessage is still streaming into —
  // it reconciles that bubble itself once the turn's own `done` arrives
  // (see chatStoreFactory.js's submitMessage), so skip here while a turn
  // is in flight rather than race it.
  if (!chatLoading.value) reloadMessages?.()
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  if (props.themeMode === 'manual') applyAspect.value = manualApplyAspectPreference.value
})
onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
  if (props.themeMode === 'manual') {
    manualApplyAspectPreference.value = applyAspect.value
    applyAspect.value = true
  }
})

function submit() {
  const text = draft.value.trim()
  // Deliberately not gated on chatLoading: a reply being generated never
  // closes the input. Whatever arrives meanwhile is answered by the next
  // turn, together with anything else waiting (see the backend's own
  // coalescing).
  if (!text || chatDisabled.value) return
  // Inside this same click/submit gesture — narration for the reply this
  // triggers plays moments later, well outside any gesture of its own.
  unlockAudioPlayback()
  handleSend(text)
  draft.value = ''
}

function resend(i) {
  handleResend(i)
}

async function startPtt(event) {
  if (event?.pointerType === 'mouse' && event.button !== 0) return
  if (recording.value || chatDisabled.value) return
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
    <div class="chat-window">
    <AppHeader
      v-if="!hideSessionsPanel"
      variant="overlay"
    >
      <template #left>
        <button
          v-if="canBackToManageProjects"
          type="button"
          class="app-header-icon-btn"
          :title="backLabel"
          @click="emit('manage-projects')"
        >«</button>
      </template>
      <template #right>
        <label
          v-if="showHumanTalkerToggle"
          class="talker-toggle"
          :class="{ 'talker-toggle-active': humanTalkerEnabled, 'talker-toggle-disabled': humanTalkerLoading }"
          title="Answer this session's next turns as a human instead of the AI (see chat/ws_human_relay.py)"
        >
          <input
            type="checkbox"
            :checked="humanTalkerEnabled"
            :disabled="humanTalkerLoading"
            @change="toggleHumanTalker"
          />
          Human
        </label>
        <ProjectsMenu
          ref="projectsMenuRef"
          session-actions
          :close-session-disabled="!selectedSessionActive"
          @select="(name) => emit('project-select', name)"
          @download="(name) => emit('project-download', name)"
          @new-session="handleNewSession"
          @close-session="handleCloseSession"
        />
        <ProfileMenu :profile="profile" @home="emit('home')" @profile="emit('profile')" @logout="emit('logout')" />
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
          :key="msg.id"
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
      v-if="chatRejected"
      class="chat-ended-notice"
    >
      Questo account è già connesso altrove. Chiudi l'altra scheda per usare la chat qui.
    </p>

    <p
      v-else-if="!chatConnected"
      class="chat-ended-notice"
    >
      Connessione alla chat non disponibile, riprovo…
    </p>

    <p
      v-else-if="chatDisabledReason"
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
        :disabled="chatDisabled || !chatConnected"
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
.talker-toggle {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.78rem;
  color: #666;
  cursor: pointer;
  user-select: none;
  margin-right: 0.4rem;
}
.talker-toggle input { cursor: pointer; }
/* Same amber used elsewhere (see RunChat.vue's own .dev-mode-toggle-active)
   for "this changes normal behavior, pay attention". */
.talker-toggle-active { color: #b06a00; font-weight: 600; }
.talker-toggle-disabled { opacity: 0.6; cursor: not-allowed; }

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

</style>
