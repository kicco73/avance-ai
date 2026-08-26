<script setup>
// Run mode's embedded live chat, full height (mode is 'edit'/'run'/'test', mutually
// exclusive, so this never shares space with Design's split-view). Auto-tracking state
// comes straight from chatStore.js's shared singleton rather than being prop-drilled.
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ChatView from '../../../chat/ChatView.vue'
import ChatTimeline from '../../../chat/ChatTimeline.vue'
import RestartFromHereButton from '../../../chat/RestartFromHereButton.vue'
import SessionsPanel from '../../../chat/SessionsPanel.vue'
import ModelMenu from '../../../ModelMenu.vue'
import { spokenTextEnabled } from '../../../../chatStoreFactory.js'
import { applyAspect } from '../../../../chatSkin.js'
import { testStore } from '../../../../testChatStore.js'

const {
  autoTrackingEnabled, autoTrackingLoading, toggleAutoTracking, handleReset,
  sessions, sessionsLoading, currentSessionId, loadSessions, selectSession, handleNewSession, handleDeleteSession,
  state, handleReact
} = testStore

defineProps({
  timeline: { type: Array, required: true },
  signalsLog: { type: Array, default: () => [] },
  selected: { type: Object, default: null },
  // Function-as-prop: the parent owns the underlying state, this component just renders.
  resolveStateLabel: { type: Function, required: true },
  resolveActionLabel: { type: Function, required: true },
  isStateGone: { type: Function, required: true },
  // Whether the project has an index.css to apply — "Apply aspect" stays
  // visible but disabled without one rather than disappearing, so the
  // toolbar's layout doesn't shift as a project gains/loses its theme.
  hasTheme: { type: Boolean, default: false }
})

const emit = defineEmits(['select-message', 'select-transition', 'restart-prefill', 'restart-resend'])

const sessionExplorerOpen = ref(false)
const sessionExplorerWidth = ref(240)
const deletingSessionId = ref(null)
let draggingSessionExplorer = false

function toggleSessionExplorer() {
  sessionExplorerOpen.value = !sessionExplorerOpen.value
  if (sessionExplorerOpen.value) loadSessions()
}

function createSession() {
  handleNewSession()
}

async function onDeleteSession(session) {
  deletingSessionId.value = session.id
  try {
    await handleDeleteSession(session)
  } finally {
    deletingSessionId.value = null
  }
}

async function onReset() {
  await handleReset()
  await loadSessions()
}

function startSessionExplorerDrag(event) {
  draggingSessionExplorer = true
  event.preventDefault()
}

function onSessionExplorerDrag(event) {
  if (!draggingSessionExplorer) return
  sessionExplorerWidth.value = Math.min(420, Math.max(160, sessionExplorerWidth.value + event.movementX))
}

function stopSessionExplorerDrag() {
  draggingSessionExplorer = false
}

onMounted(() => {
  window.addEventListener('mousemove', onSessionExplorerDrag)
  window.addEventListener('mouseup', stopSessionExplorerDrag)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onSessionExplorerDrag)
  window.removeEventListener('mouseup', stopSessionExplorerDrag)
})
</script>

<template>
  <div class="project-run-panel">
    <div
      class="run-sessions-panel"
      :class="{ 'run-sessions-panel-collapsed': !sessionExplorerOpen }"
      :style="sessionExplorerOpen ? { width: sessionExplorerWidth + 'px' } : null"
    >
      <SessionsPanel
        :sessions="sessions"
        :loading="sessionsLoading"
        :current-session-id="currentSessionId"
        :deleting-session-id="deletingSessionId"
        :collapsed="!sessionExplorerOpen"
        @update:collapsed="toggleSessionExplorer"
        @select="selectSession"
        @create="createSession"
        @delete="onDeleteSession"
      />
      <button v-if="sessionExplorerOpen" class="run-sessions-reset-btn" @click="onReset">Reset</button>
    </div>

    <div v-if="sessionExplorerOpen" class="run-split-divider" @mousedown="startSessionExplorerDrag"></div>

    <div class="edit-project-chat-panel">
      <div class="edit-project-chat-toolbar">
        <div class="edit-project-chat-toolbar-toggles">
          <label
            class="dev-mode-toggle"
            :class="{ 'dev-mode-toggle-active': !autoTrackingEnabled, 'dev-mode-toggle-disabled': autoTrackingLoading }"
          >
            <input
              type="checkbox"
              :checked="!autoTrackingEnabled"
              :disabled="autoTrackingLoading"
              @change="toggleAutoTracking"
            />
            Freeze transitions
          </label>
          <label
            class="dev-mode-toggle"
            :class="{ 'dev-mode-toggle-active': applyAspect, 'dev-mode-toggle-disabled': !hasTheme }"
            :title="hasTheme ? null : 'This project has no index.css yet.'"
          >
            <input type="checkbox" v-model="applyAspect" :disabled="!hasTheme" />
            Apply aspect
          </label>
        </div>
        <div class="edit-project-chat-toolbar-actions">
          <ModelMenu />
        </div>
      </div>
      <ChatView hide-sessions-panel theme-mode="manual" :store="testStore">
        <template #timeline>
          <ChatTimeline
            :timeline="timeline"
            :signals-log="signalsLog"
            :selected="selected"
            :spoken-text-enabled="spokenTextEnabled"
            :resolve-state-label="resolveStateLabel"
            :resolve-action-label="resolveActionLabel"
            :reactions="state?.reactions || []"
            @select-message="emit('select-message', $event)"
            @select-transition="emit('select-transition', $event)"
            @react="handleReact"
          >
            <template #message-actions="{ message }">
              <RestartFromHereButton
                v-if="message.role === 'user'"
                :disabled="isStateGone(message)"
                @click="emit('restart-resend', message)"
                @double-click="emit('restart-prefill', message)"
              />
            </template>
          </ChatTimeline>
        </template>
      </ChatView>
    </div>
  </div>
</template>

<style scoped>
.project-run-panel { flex: 1; display: flex; flex-direction: row; min-width: 0; min-height: 0; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }

.run-sessions-panel { display: flex; flex-direction: column; flex: none; min-height: 0; border-right: 1px solid #ddd; background: #f9fafb; transition: width 0.15s ease; }
.run-sessions-panel-collapsed { width: 2.4rem !important; }

.run-sessions-reset-btn { flex-shrink: 0; width: 100%; padding: 0.5rem; border: none; border-top: 1px solid #ddd; border-radius: 0; background: white; color: #c62828; font-size: 0.82rem; font-weight: 600; cursor: pointer; }
.run-sessions-reset-btn:hover { background: #c62828; color: white; }

.run-split-divider { flex-shrink: 0; width: 6px; border-radius: 3px; background: transparent; cursor: col-resize; }
.run-split-divider:hover { background: #dbe4f0; }

.edit-project-chat-panel { flex: 1; min-height: 0; min-width: 0; display: flex; flex-direction: column; }
.edit-project-chat-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; background: #f5f5f7; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.edit-project-chat-toolbar-toggles { display: flex; align-items: center; gap: 1rem; }
.edit-project-chat-toolbar-actions { display: flex; align-items: center; gap: 0.5rem; }

.dev-mode-toggle { display: flex; align-items: center; gap: 0.4rem; font-size: 0.82rem; color: #666; cursor: pointer; user-select: none; }
.dev-mode-toggle input { cursor: pointer; }
/* Same amber used elsewhere for "this changes normal behavior, pay
   attention" (see .inspector-detail-badge-current) — freezing
   transitions is a deliberate, temporary override, not the default. */
.dev-mode-toggle-active { color: #b06a00; font-weight: 600; }
.dev-mode-toggle-disabled { opacity: 0.6; cursor: not-allowed; }
.dev-mode-toggle-disabled input { cursor: not-allowed; }
</style>
