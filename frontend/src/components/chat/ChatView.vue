<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import ActionButtons from './ActionButtons.vue'
import ChatInput from './ChatInput.vue'
import MessageBubble from './MessageBubble.vue'
import ChatWaitingPanel from './ChatWaitingPanel.vue'
import ChartDialog from './ChartDialog.vue'
import ProjectsMenu from '../ProjectsMenu.vue'
import ProfileMenu from '../ProfileMenu.vue'
import MusicToggleButton from '../MusicToggleButton.vue'
import AppHeader from '../AppHeader.vue'
import SplashScreen from '../SplashScreen.vue'
import ErrorBanner from '../ErrorBanner.vue'
import { unlockAudioPlayback } from '../../audio.js'
import { liveStore } from '../../chatStore.js'
import {
  spokenTextEnabled,
  chatConnectionState,
} from '../../chatStoreFactory.js'
import { onLiveSkinApplied } from '../../chatSkin.js'
import { customDialog, infoDialog } from '../../dialogStore.js'
import { effectsScopeFor } from '../../effectsScope.js'
import { BottomAnchor } from './bottomAnchor.js'
import { SceneFade } from './sceneFade.js'

const props = defineProps({
  hideSessionsPanel: { type: Boolean, default: false },
  store: { type: Object, default: () => liveStore },
  role: { type: String, default: null },
  profile: { type: Object, default: null },
  selectable: { type: Boolean, default: false },
  selectedMessageId: { type: [Number, String], default: null }
})

const {
  state,
  buttons,
  messages,
  historyLoaded,
  chatLoading,
  chatStatus,
  actionLoading,
  draft,
  currentSessionId,
  selectedSessionActive,
  sessionEndReason,
  conversationElsewhere,
  handleNewSession,
  handleCloseSession,
  handleSend,
  handleResend,
  handleReact,
  handleAction,
  blockedReason,
  blockedDetail,
  reloadMessages,
  chart,
  dismissChart,
  backgroundAudioUrl,
  backgroundAudioPlaying,
  toggleBackgroundAudio,
  pauseBackgroundAudio
} = props.store

watch(chart, (next) => {
  if (!next) return
  customDialog({
    component: ChartDialog,
    props: { title: next.title, series: next.series, maxScale: next.maxScale },
    wide: true,
    scopeEl: effectsScopeFor(props.store.kind)
  })
  dismissChart()
})

const emit = defineEmits(['project-select', 'project-download', 'manage-projects', 'home', 'profile', 'logout', 'select-message'])

const canBackToManageProjects = computed(() => props.role === 'admin' || props.role === 'customer')
const backLabel = computed(() => props.role === 'customer' ? 'Back to App store' : 'Back to Manage projects')

const scrollEl = ref(null)
const contentEl = ref(null)
const chatInputRef = ref(null)
const rootEl = ref(null)
const shellEl = ref(null)

defineExpose({
  focus: () => chatInputRef.value?.focus()
})

const chatConnected = computed(() => chatConnectionState.value === 'open')

const chatSuperseded = computed(() => chatConnectionState.value === 'superseded')

watch(chatSuperseded, (superseded) => {
  if (!superseded) return
  infoDialog({
    title: 'Chat moved to another client',
    body: 'This conversation has been opened in another window or device, which now controls the channel. You can no longer send or receive messages from here.',
    okLabel: 'OK'
  })
})

const chatDisabled = computed(() => !state.value?.key || !state.value?.chat_enabled || !selectedSessionActive.value)

const chatDisabledReason = computed(() => {
  if (!selectedSessionActive.value) {
    if (currentSessionId.value == null) return 'No active session for this project yet.'
    if (conversationElsewhere.value) return 'This conversation is continuing on another channel.'
    if (sessionEndReason.value === 'final-state') return 'This conversation has ended.'
    return 'This session is no longer active.'
  }
  return null
})

function onVisibilityChange() {
  if (document.visibilityState !== 'visible') {
    pauseBackgroundAudio()
    return
  }
  if (!chatLoading.value) reloadMessages?.()
}

function onDocumentFocusChange(event) {
  if (rootEl.value && !rootEl.value.contains(event.target)) pauseBackgroundAudio()
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  document.addEventListener('focusin', onDocumentFocusChange)
  document.addEventListener('click', onDocumentFocusChange, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
  document.removeEventListener('focusin', onDocumentFocusChange)
  document.removeEventListener('click', onDocumentFocusChange, true)
  pauseBackgroundAudio()
})

function submit() {
  const text = draft.value.trim()
  if (!text || chatDisabled.value) return
  unlockAudioPlayback()
  handleSend(text)
  draft.value = ''
}

function resend(i) {
  handleResend(i)
}

const anchor = new BottomAnchor()

function onMessagesScroll() {
  anchor.onScroll()
}

watch([scrollEl, contentEl], ([scroller, content]) => {
  anchor.detach()
  if (scroller && content) anchor.attach(scroller, content)
})

watch(currentSessionId, () => { nextTick(() => anchor.jump()) })

onBeforeUnmount(() => anchor.detach())

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

const sceneFade = new SceneFade()

function replayScene() {
  sceneFade.replay()
}

watch(shellEl, (el) => sceneFade.attach(el))

let unregisterSkinFade = null

onMounted(() => {
  unregisterSkinFade = onLiveSkinApplied(replayScene)
})

onBeforeUnmount(() => {
  unregisterSkinFade?.()
  sceneFade.detach()
})

const prevStateKey = ref(null)
watch(
  () => state.value?.key,
  async (newKey, oldKey) => {
    if (oldKey != null) prevStateKey.value = oldKey
    await nextTick()
    replayScene()
  }
)

</script>

<template>
  <div class="chat-window-outer" ref="rootEl">
  <div
    class="chat-window-shell"
    ref="shellEl"
    :class="state?.key ? `state-${state.key}` : null"
    :data-state="state?.key ?? null"
    :data-prev-state="prevStateKey"
  >
    <div class="chat-window">
    <AppHeader
      v-if="!hideSessionsPanel || backgroundAudioUrl"
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
        <MusicToggleButton :url="backgroundAudioUrl" :playing="backgroundAudioPlaying" @toggle="toggleBackgroundAudio" />
        <template v-if="!hideSessionsPanel">
          <ProjectsMenu
            session-actions
            subscribed-only
            :close-session-disabled="!selectedSessionActive"
            @select="(name) => emit('project-select', name)"
            @download="(name) => emit('project-download', name)"
            @new-session="handleNewSession"
            @close-session="handleCloseSession"
          />
          <ProfileMenu :profile="profile" @home="emit('home')" @profile="emit('profile')" @logout="emit('logout')" />
        </template>
      </template>
    </AppHeader>

    <SplashScreen v-if="!hideSessionsPanel && blockedReason === 'paused'" variant="paused" :reason="blockedDetail" embedded />
    <SplashScreen
      v-else-if="!hideSessionsPanel && (blockedReason !== null || (historyLoaded && !state?.key))"
      variant="no-project"
      embedded
    />
    <template v-else>
    <ErrorBanner />
    <div class="chat-header">
      <div class="chat-header-icon"></div>
    </div>

    <div class="messages chat-body" ref="scrollEl" @scroll="onMessagesScroll">
      <div class="messages-content" ref="contentEl">
      <slot name="timeline">
        <template v-for="(msg, i) in messages" :key="msg.id">
          <div
            v-if="selectable"
            class="chat-message-row"
            :class="[msg.role === 'user' ? 'chat-message-row-user' : 'chat-message-row-assistant', { 'chat-message-row-selected': selectedMessageId === msg.id }]"
            @click="emit('select-message', msg)"
          >
            <span class="chat-message-row-actions" @click.stop>
              <slot name="message-actions" :message="msg" />
            </span>
            <MessageBubble
              :message="msg"
              :spoken-text-enabled="spokenTextEnabled"
              :reactions="state?.reactions || []"
              show-timestamp
              @resend="resend(i)"
              @react="handleReact(msg.messageId, $event)"
            />
          </div>
          <MessageBubble
            v-else
            :message="msg"
            :spoken-text-enabled="spokenTextEnabled"
            :reactions="state?.reactions || []"
            show-timestamp
            @resend="resend(i)"
            @react="handleReact(msg.messageId, $event)"
          />
        </template>
      </slot>
      </div>
    </div>

    <p
      v-if="!chatSuperseded && !chatConnected"
      class="chat-ended-notice"
    >
      Connection not available, trying again…
    </p>

    <p
      v-else-if="!chatSuperseded && chatDisabledReason"
      class="chat-ended-notice"
    >
      {{ chatDisabledReason }}
      <button
        v-if="conversationElsewhere"
        type="button"
        class="chat-ended-notice-action"
        @click="handleNewSession"
      >Continue here</button>
    </p>

    <div class="chat-footer">
      <ActionButtons
        v-if="selectedSessionActive"
        :actions="buttons"
        :disabled="actionLoading || !chatConnected"
        @action="onAction"
      />

      <ChatInput
        v-if="state?.chat_enabled"
        ref="chatInputRef"
        v-model="draft"
        :disabled="chatDisabled || !chatConnected"
        :store="store"
        :sample="store.sample === true"
        @submit="submit"
      />
    </div>

    <ChatWaitingPanel v-if="!historyLoaded" />
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
  animation: skin-scene-in 0.25s ease;
  container-name: chat-window;
  container-type: inline-size;
}

@keyframes skin-scene-in {
  from {
    opacity: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .chat-window-shell {
    animation: none;
  }
}

.chat-window {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
  --chat-header-height: 70px;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  overscroll-behavior-y: contain;
}

.messages-content {
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.chat-message-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.2rem 0.4rem;
  border-radius: 8px;
  cursor: pointer;
}

.chat-message-row:hover {
  background: #f7f9fc;
}

.chat-message-row-selected {
  background: #e3ebf7;
}

.chat-message-row-user {
  justify-content: flex-end;
}

.chat-message-row-assistant {
  justify-content: flex-start;
}

.chat-message-row-actions {
  display: contents;
}

.chat-ended-notice {
  color: #444;
  background: #f5f5f7;
  margin: 0;
  padding: 0.5rem 1rem;
  font-size: 0.85rem;
}

.chat-ended-notice-action {
  margin-left: 0.5rem;
  padding: 0;
  border: 0;
  background: none;
  color: #0b6bcb;
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}

.chat-header {
  flex-shrink: 0;
  height: var(--chat-header-height);
  position: relative;
  padding-top: var(--safe-area-top);
}

.chat-footer {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  padding-bottom: var(--safe-area-bottom);
}

</style>
