<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatTimeline from '../../../../../components/chat/ChatTimeline.vue'
import SessionsTree from '../../../../../components/chat/SessionsTree.vue'
import MessageCommentButton from '../../../../../components/chat/MessageCommentButton.vue'
import ProjectsMenu from '../../../../../components/ProjectsMenu.vue'
import ProfileMenu from '../../../../../components/ProfileMenu.vue'
import AppHeader from '../../../../../components/AppHeader.vue'
import Inspector from '../../inspector/Inspector.vue'
import InspectorGraphTab from '../../inspector/InspectorGraphTab.vue'
import InspectorSignalsTab from '../../inspector/InspectorSignalsTab.vue'
import InspectorUserInfoCard from '../../../../../components/skillkit/InspectorUserInfoCard.vue'
import SessionDetailCard from '../../../../../components/skillkit/SessionDetailCard.vue'
import { getProjectGraph, putSessionLabeled, putSessionTitle, putSessionComment, getUsers } from '../../../api.js'
import { sessions, sessionsLoading, loadSessions, refreshSessionsQuietly } from '../../../../../chatStore.js'
import { commentForMessage } from '../../../../../testTimeline.js'
import { useResizablePanel } from '../../../../../composables/useResizablePanel.js'
import { useSessionAnnotation } from '../../../useSessionAnnotation.js'
import { useSessionAdmin } from '../../../useSessionAdmin.js'

const props = defineProps({
  projectId: {
    type: String,
    required: true
  },
  profile: { type: Object, default: null }
})

const emit = defineEmits([
  'close', 'project-select', 'home', 'profile', 'logout'
])

const currentSessionId = ref(null)

const inspectorRef = ref(null)
const { width: inspectorWidth, startDrag: startInspectorDrag } = useResizablePanel(360, {
  min: 240, max: 560, invert: true, onResize: () => inspectorRef.value?.resize()
})
const inspectorCollapsed = ref(false)
const inspectorTabs = computed(() => [
  { id: 'info', label: 'Info' },
  { id: 'states', label: 'States' },
  { id: 'signals', label: 'Signals' }
])
const inspectorActiveTab = ref('info')
const testSessionsPanelOpen = ref(true)
const { width: sessionsPanelWidth, startDrag: startSessionsDrag } = useResizablePanel(240, { min: 160, max: 420 })

function toggleTestSessionsPanel() {
  testSessionsPanelOpen.value = !testSessionsPanelOpen.value
  if (testSessionsPanelOpen.value) loadSessions(true, props.projectId)
}

const selectedUserNode = ref(null)
const treeSelectedNodeId = computed(() =>
  selectedUserNode.value ? `user:${selectedUserNode.value}` : (currentSessionId.value != null ? `session:${currentSessionId.value}` : null)
)

function selectSession(session) {
  if (session.id === currentSessionId.value) return
  currentSessionId.value = session.id
}

function onSelectTreeNode(nodeId) {
  if (nodeId.startsWith('user:')) {
    selectedUserNode.value = nodeId.slice('user:'.length)
    currentSessionId.value = null
    return
  }
  const sessionId = Number(nodeId.slice('session:'.length))
  const session = sessions.value.find((s) => s.id === sessionId)
  if (session) selectSession(session)
}

watch(currentSessionId, (id) => {
  if (id != null) selectedUserNode.value = null
})

function handleWindowResize() {
  inspectorRef.value?.resize()
}

const currentSession = computed(() => sessions.value.find((s) => s.id === currentSessionId.value) ?? null)

const currentSessionIsImported = computed(() => currentSession.value?.type === 'imported')

const {
  loading, rawMessages, signalsLog, sessionStartState, loadTimeline, timeline,
  selected, selectMessage, selectTransition, highlightedStateKey, firedActionEdge, signalValues,
  annotatableSignalsRow, expectedState, expectedValues, annotatableExpectedSignals,
  hasAnyAnnotations, unlabelingAll, onUnlabelAll,
  onUpdateExpectedState, onUpdateExpectedSignals, onSaveComment,
} = useSessionAnnotation(props.projectId, currentSessionId, currentSessionIsImported, inspectorRef)

const {
  importingSessions, importProgress, handleImportSession,
  onMoveSessions, onDeleteTestUser, onDeleteUserSessions,
  deletingAllImported, handleDeleteAllImported,
  deletingSessionId, handleDeleteSession,
  downloadingSessions, handleDownloadSessions,
} = useSessionAdmin(props.projectId, currentSessionId, currentSession, currentSessionIsImported, selectSession)

const users = ref([])
async function loadUsers() {
  try {
    const res = await getUsers()
    users.value = res.users
  } catch {
  }
}

const sessionUser = computed(() => {
  if (currentSessionIsImported.value || !currentSession.value) return null
  return users.value.find((u) => u.id === currentSession.value.username) ?? null
})

const selectedUserProfile = computed(() => {
  if (!selectedUserNode.value) return null
  return users.value.find((u) => u.id === selectedUserNode.value) ?? null
})

const currentSessionLabeled = computed(() => {
  return sessions.value.find((s) => s.id === currentSessionId.value)?.has_annotations ?? false
})

const markingDone = ref(false)

async function onToggleMarkDone() {
  if (!currentSessionId.value) return
  markingDone.value = true
  try {
    await putSessionLabeled(currentSessionId.value, !currentSessionLabeled.value)
    await refreshSessionsQuietly(true, props.projectId)
  } catch {
  } finally {
    markingDone.value = false
  }
}

watch(selected, () => {
  nextTick(() => inspectorRef.value?.refresh())
})

onMounted(async () => {
  loadUsers()
  await loadSessions(true, props.projectId)
  const mostRecent = sessions.value[0] ?? null
  if (mostRecent) {
    selectSession(mostRecent)
  } else {
    loadTimeline()
  }
  window.addEventListener('resize', handleWindowResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleWindowResize)
})

async function handleSetSessionTitle(sessionId, title) {
  await putSessionTitle(sessionId, title)
  await refreshSessionsQuietly(true, props.projectId)
}

async function handleSetSessionComment(sessionId, comment) {
  await putSessionComment(sessionId, comment)
  await refreshSessionsQuietly(true, props.projectId)
}
</script>

<template>
  <div class="test-overlay">
    <AppHeader>
      <template #left>
        <button class="app-header-icon-btn" title="Back" @click="emit('close')">«</button>
        <ProjectsMenu align="left" :selected-name="projectId" @select="(name) => emit('project-select', name)" />
      </template>
      <template #center>
        <h2 class="app-header-title test-header-title">Sessions</h2>
      </template>
      <template #right>
        <div class="test-header-actions">
          <ProfileMenu :profile="profile" @home="emit('home')" @profile="emit('profile')" @logout="emit('logout')" />
        </div>
      </template>
    </AppHeader>

    <div class="test-body">
      <div class="test-chat-pane">
        <div class="sessions-panel-wrap">
          <div class="sessions-panel" :class="{ 'sessions-panel-collapsed': !testSessionsPanelOpen }" :style="testSessionsPanelOpen ? { width: sessionsPanelWidth + 'px' } : null">
            <SessionsTree
              :sessions="sessions"
              :users="users"
              :loading="sessionsLoading"
              :selected-node-id="treeSelectedNodeId"
              :allow-import="true"
              :importing="importingSessions"
              :import-progress="importProgress"
              :allow-download-all="true"
              :downloading-all="downloadingSessions"
              :allow-delete-all-imported="true"
              :deleting-all-imported="deletingAllImported"
              :collapsed="!testSessionsPanelOpen"
              @update:collapsed="toggleTestSessionsPanel"
              @select="onSelectTreeNode"
              @import="handleImportSession"
              @download-all="handleDownloadSessions"
              @delete-all-imported="handleDeleteAllImported"
              @move-sessions="onMoveSessions"
              @delete-test-user="onDeleteTestUser"
              @delete-user-sessions="onDeleteUserSessions"
            />
          </div>
          <div v-if="testSessionsPanelOpen" class="split-divider" @mousedown="startSessionsDrag"></div>
        </div>

        <div class="test-chat-content">
          <div class="test-chat-toolbar">
            <span class="test-chat-title">Chat</span>
            <div class="test-chat-toolbar-actions">
              <button
                type="button"
                class="test-unlabel-all-btn"
                :disabled="!hasAnyAnnotations || unlabelingAll"
                @click="onUnlabelAll"
              >
                {{ unlabelingAll ? 'Unlabelling…' : 'Unlabel all' }}
              </button>
              <button
                type="button"
                class="test-mark-done-btn"
                :class="{ 'test-mark-done-btn-active': currentSessionLabeled }"
                :disabled="!currentSessionId || markingDone"
                @click="onToggleMarkDone"
              >
                {{ currentSessionLabeled ? '✓ Done' : 'Mark done' }}
              </button>
            </div>
          </div>

          <p v-if="loading" class="test-status">Loading…</p>
          <p v-else-if="!currentSessionId" class="test-status">
            Please select a session.
          </p>
          <p v-else-if="!timeline.length" class="test-status">This session has no messages yet.</p>

          <ChatTimeline
            v-else
            :timeline="timeline"
            :signals-log="signalsLog"
            :selected="selected"
            :imported="currentSessionIsImported"
            :auto-scroll="false"
            @select-message="selectMessage"
            @select-transition="selectTransition"
          >
            <template #message-actions="{ message }">
              <MessageCommentButton
                :comment="commentForMessage(message, signalsLog)"
                @save="(comment) => onSaveComment(message.id, comment)"
              />
            </template>
          </ChatTimeline>
        </div>
      </div>

      <div class="split-divider inspector-divider" @mousedown="startInspectorDrag"></div>

      <div
        class="test-inspector-panel"
        :class="{ 'test-inspector-panel-collapsed': inspectorCollapsed }"
        :style="inspectorCollapsed ? null : { '--inspector-width': inspectorWidth + 'px' }"
      >
        <Inspector
          ref="inspectorRef"
          :tabs="inspectorTabs"
          v-model:active-tab="inspectorActiveTab"
          v-model:collapsed="inspectorCollapsed"
        >
          <template #tab-info>
            <div v-if="currentSession" class="test-session-info">
              <InspectorUserInfoCard v-if="sessionUser" :user="sessionUser" />
              <SessionDetailCard
                :session="currentSession"
                deletable
                @set-title="handleSetSessionTitle"
                @set-comment="handleSetSessionComment"
                @delete="handleDeleteSession"
              />
            </div>
            <div v-else-if="selectedUserProfile" class="test-session-info">
              <InspectorUserInfoCard :user="selectedUserProfile" />
            </div>
            <p v-else class="test-session-info-empty">Please select a session.</p>
          </template>
          <template #tab-states="{ registerTab }">
            <InspectorGraphTab
              :ref="registerTab('states')"
              :project-id="projectId"
              :highlighted-state-key="highlightedStateKey"
              :fired-action-edge="firedActionEdge"
              :annotatable="annotatableSignalsRow != null"
              :expected-state="expectedState"
              :imported="currentSessionIsImported"
              :session-id="currentSessionId"
              @update-expected-state="onUpdateExpectedState"
            />
          </template>
          <template #tab-signals="{ registerTab }">
            <InspectorSignalsTab
              :ref="registerTab('signals')"
              :project-id="projectId"
              :signal-values="signalValues"
              :annotatable="annotatableExpectedSignals"
              :expected-values="expectedValues"
              :state-key="highlightedStateKey"
              :imported="currentSessionIsImported"
              :session-id="currentSessionId"
              @update-expected-signals="onUpdateExpectedSignals"
            />
          </template>
        </Inspector>
      </div>
    </div>
  </div>
</template>

<style scoped>
.test-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  box-sizing: border-box;
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.test-header-title {
  color: #4a6fa5;
}

.test-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.test-header-actions .projects-menu {
  max-width: 220px;
}

.sessions-toggle-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.sessions-toggle-btn:hover {
  background: #eef2f9;
}

.sessions-toggle-btn-on {
  background: #4a6fa5;
  color: white;
}

.sessions-toggle-btn-on:hover {
  background: #3d5c8a;
}

.test-body {
  flex: 1;
  display: flex;
  min-height: 0;
  padding: 1rem;
}

.test-chat-pane {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: row;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
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

.sessions-panel-collapsed {
  width: 2.4rem !important;
}

.test-chat-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.test-chat-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #f5f5f7;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

.test-chat-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.test-chat-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.test-unlabel-all-btn {
  padding: 0.3rem 0.7rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
  font-size: 0.78rem;
}

.test-unlabel-all-btn:hover:not(:disabled) {
  background: #c62828;
  color: white;
}

.test-unlabel-all-btn:disabled {
  border-color: #ccc;
  color: #ccc;
  cursor: not-allowed;
}

.test-mark-done-btn {
  padding: 0.3rem 0.7rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: white;
  color: #2e7d32;
  cursor: pointer;
  font-size: 0.78rem;
}

.test-mark-done-btn:hover:not(:disabled) {
  background: #2e7d32;
  color: white;
}

.test-mark-done-btn-active {
  background: #2e7d32;
  color: white;
}

.test-mark-done-btn:disabled {
  border-color: #ccc;
  color: #ccc;
  cursor: not-allowed;
}

.test-status {
  margin: auto;
  color: #444;
}

.split-divider {
  flex-shrink: 0;
  width: 6px;
  margin: 0 0.4rem;
  border-radius: 3px;
  background: transparent;
  cursor: col-resize;
}

.split-divider:hover {
  background: #dbe4f0;
}

.test-inspector-panel {
  flex-shrink: 0;
  width: var(--inspector-width);
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: width 0.15s ease;
}

.test-inspector-panel-collapsed {
  width: 2.4rem !important;
}

.test-session-info {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.test-session-info-label {
  display: block;
  margin-top: 20px;
  font-size: 0.68rem;
  font-weight: 600;
  color: #777;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}

.test-session-info-value {
  margin: 0.15rem 0 0;
  font-size: 0.85rem;
  color: #333;
  word-break: break-word;
}

.test-session-info-empty {
  margin: 0;
  color: #666;
  font-size: 0.85rem;
}
</style>
