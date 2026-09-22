<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import SessionsPanel from '../../../../../../components/chat/SessionsPanel.vue'
import AspectMenu from './AspectMenu.vue'
import { aspectFor } from './aspects.js'
import { getHistory, getProjectFiles, putProjectFileBinary } from '../../../../api.js'
import { totalTokenBudgetPerSession } from '../../../../../../chatStoreFactory.js'
import { infoDialog } from '../../../../../../dialogStore.js'
import { testStore } from '../../../../testChatStore.js'
import { buildEmbedUrl } from '../../../../../../embedParams.js'
import { useTokensBar } from '../../../../../../composables/useTokensBar.js'
import { useFloatingTooltip } from '../../../../../../useFloatingTooltip.js'

const {
  actuatorsEnabled, actuatorsLoading, toggleActuators,
  sessions, sessionsLoading, currentSessionId, currentProjectId, loadSessions, selectSession, handleNewSession, handleDeleteSession,
  turnCount
} = testStore

const sessionTokensBurnt = ref(0)
async function refreshSessionTokensBurnt() {
  const sessionId = currentSessionId.value
  if (sessionId == null) {
    sessionTokensBurnt.value = 0
    return
  }
  try {
    const history = await getHistory(sessionId)
    sessionTokensBurnt.value = history
      .filter((m) => m.role === 'user')
      .reduce((sum, m) => sum + (m.tokens ?? 0), 0)
  } catch {
  }
}
watch(currentSessionId, refreshSessionTokensBurnt, { immediate: true })
watch(turnCount, refreshSessionTokensBurnt)

const { width: tokensBarWidth, level: tokensBarLevel } = useTokensBar(sessionTokensBurnt, totalTokenBudgetPerSession)
const {
  visible: tokensTooltipVisible, style: tokensTooltipStyle, show: showTokensTooltip, hide: hideTokensTooltip
} = useFloatingTooltip()

const props = defineProps({
  timeline: { type: Array, required: true },
  isStateGone: { type: Function, required: true }
})

const emit = defineEmits(['select-message', 'restart-prefill', 'restart-resend', 'media-saved'])

function timelineMessageFor(rawMessage) {
  return props.timeline.find((entry) => entry.kind === 'message' && entry.message.key === rawMessage.id)?.message ?? rawMessage
}

function onSelectMessage(rawMessage) {
  emit('select-message', timelineMessageFor(rawMessage))
}

let pendingSnapshotResolve = null
let pendingRestartResolve = null

function onEmbedMessage(event) {
  if (event.origin !== window.location.origin) return
  const data = event.data
  if (!data || data.source !== 'run-chat-embed') return
  if (data.type === 'snapshot-captured') {
    pendingSnapshotResolve?.(data.blob)
    pendingSnapshotResolve = null
    return
  }
  if (data.type === 'session-restarted') {
    pendingRestartResolve?.(data.sessionId)
    pendingRestartResolve = null
    return
  }
  const rawMessage = testStore.messages.value.find((m) => m.messageId === data.messageId)
  if (!rawMessage) return
  if (data.type === 'select-message') {
    onSelectMessage(rawMessage)
  } else if (data.type === 'restart-resend' || data.type === 'restart-prefill') {
    if (props.isStateGone(timelineMessageFor(rawMessage))) return
    emit(data.type, rawMessage)
  }
}

const sessionExplorerOpen = ref(false)
const sessionExplorerWidth = ref(240)
const deletingSessionId = ref(null)
let draggingSessionExplorer = false

const chatIframeEl = ref(null)
defineExpose({
  focus: () => chatIframeEl.value?.focus()
})

const iframeSrc = computed(() => {
  if (!currentProjectId.value || !currentSessionId.value) return null
  return buildEmbedUrl('test-chat', currentProjectId.value, currentSessionId.value)
})

const aspect = ref('dynamic')
const currentAspect = computed(() => aspectFor(aspect.value))
const stageEl = ref(null)
const capturingSnapshot = ref(false)

const stageWrapEl = ref(null)
const availableStageSize = ref({ width: 0, height: 0 })
let stageResizeObserver = null

function updateAvailableStageSize() {
  const el = stageWrapEl.value
  if (!el) return
  const style = getComputedStyle(el)
  const paddingX = Number.parseFloat(style.paddingLeft) + Number.parseFloat(style.paddingRight)
  const paddingY = Number.parseFloat(style.paddingTop) + Number.parseFloat(style.paddingBottom)
  availableStageSize.value = { width: el.clientWidth - paddingX, height: el.clientHeight - paddingY }
}

const fitScale = computed(() => {
  if (!currentAspect.value.width) return 1
  const { width: availW, height: availH } = availableStageSize.value
  if (!availW || !availH) return 1
  return Math.min(1, availW / currentAspect.value.width, availH / currentAspect.value.height)
})

const stageViewportStyle = computed(() => {
  if (!currentAspect.value.width) return null
  return {
    width: `${currentAspect.value.width * fitScale.value}px`,
    height: `${currentAspect.value.height * fitScale.value}px`
  }
})

const stageScalerStyle = computed(() => {
  if (!currentAspect.value.width) return null
  return {
    width: `${currentAspect.value.width}px`,
    height: `${currentAspect.value.height}px`,
    transform: `scale(${fitScale.value})`
  }
})

function snapshotFileNamesFrom(files) {
  return files.filter((name) => name.startsWith('media/snapshot-') && name.endsWith('.jpg'))
}

function nextSnapshotNumber(files, prefix) {
  const numbers = snapshotFileNamesFrom(files)
    .filter((name) => name.startsWith(prefix))
    .map((name) => Number.parseInt(name.slice(prefix.length, -'.jpg'.length), 10))
    .filter((n) => Number.isInteger(n))
  return numbers.length ? Math.max(...numbers) + 1 : 1
}

async function captureSnapshot() {
  if (capturingSnapshot.value || !chatIframeEl.value) return
  capturingSnapshot.value = true
  try {
    const blob = await new Promise((resolve) => {
      pendingSnapshotResolve = resolve
      chatIframeEl.value.contentWindow.postMessage(
        { source: 'run-chat-parent', type: 'capture-snapshot' }, window.location.origin
      )
      setTimeout(() => {
        if (pendingSnapshotResolve === resolve) pendingSnapshotResolve = null
        resolve(null)
      }, 10000)
    })
    if (!blob) return
    const projectId = currentProjectId.value
    const { files } = await getProjectFiles(projectId)
    const prefix = `media/snapshot-${aspect.value}-`
    const fileName = `${prefix}${nextSnapshotNumber(files, prefix)}.jpg`
    await putProjectFileBinary(projectId, fileName, blob)
    emit('media-saved', fileName)
    await infoDialog({
      title: 'Snapshot saved',
      body: `Photo saved in media as "${fileName.slice('media/'.length)}".`
    })
  } finally {
    capturingSnapshot.value = false
  }
}

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

async function onClearSession() {
  if (!chatIframeEl.value?.contentWindow) return
  const newSessionId = await new Promise((resolve) => {
    pendingRestartResolve = resolve
    chatIframeEl.value.contentWindow.postMessage({ source: 'run-chat-parent', type: 'restart-session' }, window.location.origin)
    setTimeout(() => {
      if (pendingRestartResolve === resolve) pendingRestartResolve = null
      resolve(null)
    }, 10000)
  })
  if (newSessionId == null) return
  await selectSession({ id: newSessionId, current: true })
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
  window.addEventListener('message', onEmbedMessage)
  updateAvailableStageSize()
  stageResizeObserver = new ResizeObserver(updateAvailableStageSize)
  if (stageWrapEl.value) stageResizeObserver.observe(stageWrapEl.value)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onSessionExplorerDrag)
  window.removeEventListener('mouseup', stopSessionExplorerDrag)
  window.removeEventListener('message', onEmbedMessage)
  stageResizeObserver?.disconnect()
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
            :class="{ 'dev-mode-toggle-active': actuatorsEnabled, 'dev-mode-toggle-disabled': actuatorsLoading }"
          >
            <input
              type="checkbox"
              :checked="actuatorsEnabled"
              :disabled="actuatorsLoading"
              @change="toggleActuators"
            />
            Run external actuators
          </label>
        </div>
        <button
          v-if="currentAspect.width"
          type="button"
          class="run-snapshot-btn"
          title="Save a snapshot of this aspect to the project media"
          :disabled="capturingSnapshot"
          @click="captureSnapshot"
        >
          <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor">
            <path d="M9.4 4L7.6 6H4c-1.1 0-2 .9-2 2v11c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-3.6L14.6 4H9.4zM12 9c2.76 0 5 2.24 5 5s-2.24 5-5 5-5-2.24-5-5 2.24-5 5-5zm0 2c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/>
          </svg>
        </button>
        <AspectMenu v-model="aspect" />
        <button type="button" class="run-clear-session-btn" title="Delete this session and start a new one" @click="onClearSession">Restart</button>
      </div>
      <Teleport to="body">
        <span v-if="tokensTooltipVisible" class="run-tokens-tooltip-floating" :style="tokensTooltipStyle">Token burnt: {{ sessionTokensBurnt }}</span>
      </Teleport>
      <div ref="stageWrapEl" class="edit-project-chat-stage" :class="{ 'edit-project-chat-stage-constrained': !!currentAspect.width }">
        <div class="edit-project-chat-viewport" :style="stageViewportStyle">
          <div class="edit-project-chat-scaler" :style="stageScalerStyle">
            <div ref="stageEl" class="edit-project-chat-frame">
              <iframe
                v-if="iframeSrc"
                ref="chatIframeEl"
                :src="iframeSrc"
                class="edit-project-chat-iframe"
                title="Live test chat"
              ></iframe>
              <div v-else class="edit-project-chat-frame-empty">No active session</div>
            </div>
          </div>
        </div>
      </div>
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
.edit-project-chat-toolbar-toggles { display: flex; align-items: center; gap: 1rem; margin-left: auto; }

.run-clear-session-btn { flex-shrink: 0; padding: 0.35rem 0.75rem; border: 1px solid #c62828; border-radius: 6px; background: white; color: #c62828; font-size: 0.82rem; font-weight: 600; cursor: pointer; }
.run-clear-session-btn:hover { background: #c62828; color: white; }

.run-snapshot-btn { flex-shrink: 0; display: flex; align-items: center; justify-content: center; width: 1.9rem; height: 1.9rem; padding: 0; border: 1px solid #4a6fa5; border-radius: 6px; background: white; color: #4a6fa5; cursor: pointer; }
.run-snapshot-btn:hover:not(:disabled) { background: #4a6fa5; color: white; }
.run-snapshot-btn:disabled { opacity: 0.6; cursor: not-allowed; }

.dev-mode-toggle { display: flex; align-items: center; gap: 0.4rem; font-size: 0.82rem; color: #666; cursor: pointer; user-select: none; }
.dev-mode-toggle input { cursor: pointer; }
.dev-mode-toggle-active { color: #b06a00; font-weight: 600; }
.dev-mode-toggle-disabled { opacity: 0.6; cursor: not-allowed; }
.dev-mode-toggle-disabled input { cursor: not-allowed; }

.edit-project-chat-stage { flex: 1; min-height: 0; min-width: 0; display: flex; overflow: hidden; background: #eef0f3; }
.edit-project-chat-stage-constrained { justify-content: center; align-items: center; padding: 1.5rem; }
.edit-project-chat-viewport { display: flex; flex: 1; min-height: 0; min-width: 0; }
.edit-project-chat-stage-constrained .edit-project-chat-viewport { flex: none; border-radius: 10px; box-shadow: 0 4px 24px rgba(0, 0, 0, 0.18); overflow: hidden; }
.edit-project-chat-scaler { display: flex; flex: 1; min-height: 0; min-width: 0; transform-origin: top left; }
.edit-project-chat-stage-constrained .edit-project-chat-scaler { flex: none; }
.edit-project-chat-frame { flex: 1; min-height: 0; min-width: 0; display: flex; background: white; }
.edit-project-chat-iframe { display: block; width: 100%; height: 100%; border: 0; }
.edit-project-chat-frame-empty { flex: 1; display: flex; align-items: center; justify-content: center; color: #999; font-size: 0.85rem; }

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
