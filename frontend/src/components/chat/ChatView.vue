<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import ActionButtons from './ActionButtons.vue'
import ChatInput from './ChatInput.vue'
import MessageBubble from './MessageBubble.vue'
import ChatSupersededOverlay from './ChatSupersededOverlay.vue'
import ChatWaitingPanel from './ChatWaitingPanel.vue'
import ProjectsMenu from '../ProjectsMenu.vue'
import ProfileMenu from '../ProfileMenu.vue'
import AppHeader from '../AppHeader.vue'
import SplashScreen from '../SplashScreen.vue'
import { setApiError } from '../../errorStore.js'
import { unlockAudioPlayback } from '../../audio.js'
import { liveStore } from '../../chatStore.js'
import {
  spokenTextEnabled,
  chatConnectionState,
} from '../../chatStoreFactory.js'
import { applyAspect, manualApplyAspectPreference } from '../../chatSkin.js'

const props = defineProps({
  hideSessionsPanel: { type: Boolean, default: false },
  themeMode: { type: String, default: 'auto' },
  store: { type: Object, default: () => liveStore },
  role: { type: String, default: null },
  profile: { type: Object, default: null }
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
  reloadMessages
} = props.store

const emit = defineEmits(['project-select', 'project-download', 'manage-projects', 'home', 'profile', 'logout'])

const canBackToManageProjects = computed(() => props.role === 'admin' || props.role === 'customer')
const backLabel = computed(() => props.role === 'customer' ? 'Back to App store' : 'Back to Manage projects')

const scrollEl = ref(null)
const chatInputRef = ref(null)

defineExpose({
  focus: () => chatInputRef.value?.focus()
})

const chatConnected = computed(() => chatConnectionState.value === 'open')

const chatSuperseded = computed(() => chatConnectionState.value === 'superseded')

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
  if (document.visibilityState !== 'visible') return
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
  if (!text || chatDisabled.value) return
  unlockAudioPlayback()
  handleSend(text)
  draft.value = ''
}

function resend(i) {
  handleResend(i)
}

function scrollToBottom() {
  nextTick(() => {
    if (scrollEl.value) {
      scrollEl.value.scrollTop = scrollEl.value.scrollHeight
    }
  })
}

const NEAR_BOTTOM_THRESHOLD_PX = 80
const userNearBottom = ref(true)

function onMessagesScroll() {
  const el = scrollEl.value
  if (!el) return
  userNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_THRESHOLD_PX
}

watch(currentSessionId, () => { userNearBottom.value = true })

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

const prevStateKey = ref(null)
watch(
  () => state.value?.key,
  (newKey, oldKey) => {
    if (oldKey != null) prevStateKey.value = oldKey
  }
)

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
        <ProjectsMenu
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

    <SplashScreen v-if="!hideSessionsPanel && blockedReason === 'paused'" variant="paused" :reason="blockedDetail" embedded />
    <SplashScreen
      v-else-if="!hideSessionsPanel && (blockedReason !== null || (historyLoaded && !state?.key))"
      variant="no-project"
      embedded
    />
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
    <ChatSupersededOverlay v-else-if="chatSuperseded" />
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
  --chat-header-height: 70px;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  overscroll-behavior-y: contain;
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
