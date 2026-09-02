<script setup>
// Run mode's embedded live chat, full height (mode is 'edit'/'run'/'test', mutually
// exclusive, so this never shares space with Design's split-view). Auto-tracking state
// comes straight from chatStore.js's shared singleton rather than being prop-drilled.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatView from '../../../chat/ChatView.vue'
import ChatTimeline from '../../../chat/ChatTimeline.vue'
import RestartFromHereButton from '../../../chat/RestartFromHereButton.vue'
import SessionsPanel from '../../../chat/SessionsPanel.vue'
import { clearEnv, getMessages, deleteSession } from '../../../../api.js'
import { spokenTextEnabled, totalTokenBudgetPerSession } from '../../../../chatStoreFactory.js'
import { applyAspect } from '../../../../chatSkin.js'
import { testStore } from '../../../../testChatStore.js'
import { useTokensBar } from '../../../../composables/useTokensBar.js'
import { useFloatingTooltip } from '../../../../useFloatingTooltip.js'

const {
  autoTrackingEnabled, autoTrackingLoading, toggleAutoTracking,
  actuatorsEnabled, actuatorsLoading, toggleActuators,
  sessions, sessionsLoading, currentSessionId, loadSessions, selectSession, handleNewSession, handleDeleteSession,
  state, handleReact, turnCount
} = testStore

// Input tokens burnt so far in the live session — same source
// (getMessages' own per-message `tokens`) and math as EditProjectView.
// vue's own autoSessionInputTokens, just event-driven off this store's
// currentSessionId/turnCount instead of a session picked in Test mode.
// Naturally reads as zero the moment a new/switched session has no
// messages of its own yet — no separate reset needed.
const sessionTokensBurnt = ref(0)
async function refreshSessionTokensBurnt() {
  const sessionId = currentSessionId.value
  if (sessionId == null) {
    sessionTokensBurnt.value = 0
    return
  }
  try {
    const history = await getMessages(sessionId)
    sessionTokensBurnt.value = history
      .filter((m) => m.role === 'user')
      .reduce((sum, m) => sum + (m.tokens ?? 0), 0)
  } catch {
    // already surfaced via apiFetch
  }
}
watch(currentSessionId, refreshSessionTokensBurnt, { immediate: true })
watch(turnCount, refreshSessionTokensBurnt)

const { width: tokensBarWidth, level: tokensBarLevel } = useTokensBar(sessionTokensBurnt, totalTokenBudgetPerSession)
const {
  visible: tokensTooltipVisible, style: tokensTooltipStyle, show: showTokensTooltip, hide: hideTokensTooltip
} = useFloatingTooltip()

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

const chatViewRef = ref(null)
defineExpose({
  focus: () => chatViewRef.value?.focus()
})

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

// Central toolbar's own "Clear" button — deletes the current session
// outright and immediately opens a brand new one, no confirmation (test
// sessions are cheap/disposable, same reasoning as handleNewSession's
// own confirmNewSession: false for this store — see testChatStore.js).
async function onClearSession() {
  const sessionId = currentSessionId.value
  try {
    if (sessionId != null) await deleteSession(sessionId)
    await clearEnv()
  } catch {
    // already surfaced via apiFetch
  }
  await handleNewSession()
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
    </div>

    <div v-if="sessionExplorerOpen" class="run-split-divider" @mousedown="startSessionExplorerDrag"></div>

    <div class="edit-project-chat-panel">
      <div class="edit-project-chat-toolbar">
        <div
          v-if="totalTokenBudgetPerSession != null"
          class="run-tokens-bar"
          @mouseenter="showTokensTooltip($event.currentTarget)"
          @mouseleave="hideTokensTooltip"
        >
          <span class="run-tokens-icon">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
          </span>
          <span class="run-tokens-label">Tokens</span>
          <div class="run-tokens-bar-track">
            <div
              class="run-tokens-bar-fill"
              :class="`run-tokens-bar-fill-${tokensBarLevel}`"
              :style="{ width: tokensBarWidth }"
            ></div>
          </div>
        </div>
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
            :class="{ 'dev-mode-toggle-active': actuatorsEnabled, 'dev-mode-toggle-disabled': actuatorsLoading }"
          >
            <input
              type="checkbox"
              :checked="actuatorsEnabled"
              :disabled="actuatorsLoading"
              @change="toggleActuators"
            />
            Run actuators
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
        <button type="button" class="run-clear-session-btn" title="Delete this session and start a new one" @click="onClearSession">Clear</button>
      </div>
      <Teleport to="body">
        <span v-if="tokensTooltipVisible" class="run-tokens-tooltip-floating" :style="tokensTooltipStyle">Token burnt: {{ sessionTokensBurnt }}</span>
      </Teleport>
      <ChatView ref="chatViewRef" hide-sessions-panel theme-mode="manual" :store="testStore">
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

.run-split-divider { flex-shrink: 0; width: 6px; border-radius: 3px; background: transparent; cursor: col-resize; }
.run-split-divider:hover { background: #dbe4f0; }

.edit-project-chat-panel { flex: 1; min-height: 0; min-width: 0; display: flex; flex-direction: column; }
.edit-project-chat-toolbar { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0.75rem; background: #f5f5f7; border-bottom: 1px solid #ddd; flex-shrink: 0; }
/* Pinned right regardless of whether the tokens bar renders beside it
   (hidden when total-token-budget-per-session isn't configured). */
.edit-project-chat-toolbar-toggles { display: flex; align-items: center; gap: 1rem; margin-left: auto; }

.run-clear-session-btn { flex-shrink: 0; padding: 0.35rem 0.75rem; border: 1px solid #c62828; border-radius: 6px; background: white; color: #c62828; font-size: 0.82rem; font-weight: 600; cursor: pointer; }
.run-clear-session-btn:hover { background: #c62828; color: white; }

.dev-mode-toggle { display: flex; align-items: center; gap: 0.4rem; font-size: 0.82rem; color: #666; cursor: pointer; user-select: none; }
.dev-mode-toggle input { cursor: pointer; }
/* Same amber used elsewhere for "this changes normal behavior, pay
   attention" (see .inspector-detail-badge-current) — freezing
   transitions is a deliberate, temporary override, not the default. */
.dev-mode-toggle-active { color: #b06a00; font-weight: 600; }
.dev-mode-toggle-disabled { opacity: 0.6; cursor: not-allowed; }
.dev-mode-toggle-disabled input { cursor: not-allowed; }

/* Same shape as ProjectTestPanel.vue's own .tests-panel-tokens-bar —
   this one tracks the live session's own input tokens against
   total-token-budget-per-session instead of a test-run budget. */
.run-tokens-bar { display: flex; align-items: center; gap: 0.4rem; min-width: 160px; }
.run-tokens-icon { flex-shrink: 0; display: flex; color: #4a6fa5; }
.run-tokens-label { font-size: 0.8rem; color: #555; white-space: nowrap; }
.run-tokens-bar-track { width: 240px; height: 8px; border-radius: 999px; background: #eee; overflow: hidden; }
.run-tokens-bar-fill { height: 100%; border-radius: 999px; transition: width 0.3s ease; }
.run-tokens-bar-fill-green { background: #2e7d32; }
.run-tokens-bar-fill-orange { background: #f5a623; }
.run-tokens-bar-fill-red { background: #c62828; }
</style>

<style>
/* Unscoped: teleported to <body> (see ProjectTestPanel.vue's own tokens
   bar tooltip), outside this component's normal DOM subtree. */
.run-tokens-tooltip-floating {
  position: fixed;
  width: max-content;
  max-width: 200px;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  background: #333;
  color: white;
  font-size: 0.72rem;
  font-weight: 400;
  line-height: 1.3;
  text-align: left;
  pointer-events: none;
  z-index: 1000;
}
</style>
