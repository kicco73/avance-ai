<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatTimeline from '../../chat/ChatTimeline.vue'
import SessionsTree from '../../chat/SessionsTree.vue'
import MessageCommentButton from '../../chat/MessageCommentButton.vue'
import ProjectsMenu from '../../ProjectsMenu.vue'
import SettingsMenu from '../../settings/SettingsMenu.vue'
import ProfileMenu from '../../ProfileMenu.vue'
import Inspector from '../../inspector/Inspector.vue'
import InspectorGraphTab from '../../inspector/InspectorGraphTab.vue'
import InspectorSignalsTab from '../../inspector/InspectorSignalsTab.vue'
import InspectorUserInfoCard from '../../inspector/InspectorUserInfoCard.vue'
import SessionDetailCard from '../../inspector/SessionDetailCard.vue'
import { getProjectGraph, putSessionLabeled, getUsers } from '../../../api.js'
import { sessions, sessionsLoading, loadSessions, refreshSessionsQuietly } from '../../../chatStore.js'
import { commentForMessage } from '../../../testTimeline.js'
import { useResizablePanel } from '../../../composables/useResizablePanel.js'
import { useSessionAnnotation } from '../../../composables/useSessionAnnotation.js'
import { useSessionAdmin } from '../../../composables/useSessionAdmin.js'

const props = defineProps({
  projectName: {
    type: String,
    required: true
  },
  // This is a supervisor's own landing page now (see App.vue's role-based
  // routing) — no Back button to fall back on, so its own Settings menu
  // (same one the main chat screen shows) is how it reaches every other
  // top-level view instead.
  role: { type: String, default: null },
  // ProfileMenu.vue's own avatar/name — App.vue already fetched this once
  // during boot (see its own `profile` prop docstring), passed straight
  // through so this landing page can show the same topbar avatar the main
  // chat screen does.
  profile: { type: Object, default: null }
})

// The Settings-menu ones (manage-projects/manage-users/label-sessions/
// about/download-backup/restore-backup) are a plain pass-through of
// SettingsMenu.vue's own emits; profile/logout are the same pass-through
// of ProfileMenu.vue's own.
const emit = defineEmits([
  'close', 'project-select', 'manage-projects', 'manage-users', 'label-sessions', 'edit-projects', 'about',
  'download-backup', 'restore-backup', 'profile', 'logout'
])

// This view's own session pointer — never chatStore.js's shared
// currentSessionId. Browsing/reviewing a session here (including an
// imported one, which can never be "the" live session) must not leak
// into what the main chat window is showing.
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
// Starts open — reviewing a specific session is the point of this view.
const testSessionsPanelOpen = ref(true)
const { width: sessionsPanelWidth, startDrag: startSessionsDrag } = useResizablePanel(240, { min: 160, max: 420 })

// Independent of the main page's own Sessions panel state
// (chatStore.js's sessionsPanelOpen) — this overlay has its own panel.
function toggleTestSessionsPanel() {
  testSessionsPanelOpen.value = !testSessionsPanelOpen.value
  if (testSessionsPanelOpen.value) loadSessions(true, props.projectName)
}

// This view's Sessions panel reviews imported transcripts alongside live
// ones, so every load/refresh below passes includeImported.

// SessionsTree's own node id, either `user:<username>` (a user branch was
// clicked, not one of their sessions) or `session:<id>`. Kept separate
// from this view's own currentSessionId so the tree can highlight a
// selected user even though that's not a session — see onSelectTreeNode below.
const selectedUserNode = ref(null)
const treeSelectedNodeId = computed(() =>
  selectedUserNode.value ? `user:${selectedUserNode.value}` : (currentSessionId.value != null ? `session:${currentSessionId.value}` : null)
)

// This view's own session switch — unlike chatStore.js's selectSession,
// never touches the main chat window's messages/state, only this view's
// own currentSessionId (which loadTimeline below reacts to).
function selectSession(session) {
  if (session.id === currentSessionId.value) return
  currentSessionId.value = session.id
}

// A user branch has no session of its own, so it just clears the active
// session, which the chat pane and Info tab then both render as "Please
// select a session."
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

// A session picked some other way (import, auto-select on load) should
// clear a stale user-branch highlight so the tree's own selection follows.
watch(currentSessionId, (id) => {
  if (id != null) selectedUserNode.value = null
})

function handleWindowResize() {
  inspectorRef.value?.resize()
}

// The currently-selected session's row out of the shared sessions list
// (chatStore.js). Null before the list has loaded, or if its id has
// since been deleted out from under it.
const currentSession = computed(() => sessions.value.find((s) => s.id === currentSessionId.value) ?? null)

// Whether the session currently being reviewed was imported rather than
// played live — the one case with no real Tracking rows for
// annotatableSignalsRow below to consult.
const currentSessionIsImported = computed(() => currentSession.value?.type === 'imported')

const {
  loading, rawMessages, signalsLog, sessionStartState, loadTimeline, timeline,
  selected, selectMessage, selectTransition, highlightedStateKey, firedActionEdge, signalValues,
  annotatableSignalsRow, expectedState, expectedValues, annotatableExpectedSignals,
  hasAnyAnnotations, unlabelingAll, onUnlabelAll,
  onUpdateExpectedState, onUpdateExpectedSignals, onSaveComment,
} = useSessionAnnotation(props.projectName, currentSessionId, currentSessionIsImported, inspectorRef)

const {
  importingSessions, importProgress, handleImportSession,
  onMoveSessions, onDeleteTestUser, onDeleteUserSessions,
  deletingAllImported, handleDeleteAllImported,
  deletingSessionId, handleDeleteSession,
  downloadingSessions, handleDownloadSessions,
} = useSessionAdmin(props.projectName, currentSessionId, currentSession, currentSessionIsImported, selectSession)

// Every registered user, fetched once — same list ManageUsersView.vue
// shows, reused here just to resolve a live session's `username` (the
// user's own id/email) into a full profile for the Info tab's card below.
// GET /api/users is supervisor-and-up, and every entry point into this
// view already requires at least supervisor (see App.vue), so this is
// always reachable here.
const users = ref([])
async function loadUsers() {
  try {
    const res = await getUsers()
    users.value = res.users
  } catch {
    // already surfaced via apiFetch
  }
}

// null for an imported session (no real user) or before the users list
// or a match resolves — the Info tab only renders the card once this is set.
const sessionUser = computed(() => {
  if (currentSessionIsImported.value || !currentSession.value) return null
  return users.value.find((u) => u.id === currentSession.value.username) ?? null
})

// The user whose branch (not a session within it) is selected in the
// sessions tree — the Info tab shows their profile card in place of the
// session card while this is set.
const selectedUserProfile = computed(() => {
  if (!selectedUserNode.value) return null
  return users.value.find((u) => u.id === selectedUserNode.value) ?? null
})

// The persisted "reviewed" flag, read off the Sessions panel's list —
// unlike hasAnyAnnotations above, "is there anything to clear" is a
// different question than "has an expert signed off".
const currentSessionLabeled = computed(() => {
  return sessions.value.find((s) => s.id === currentSessionId.value)?.has_annotations ?? false
})

const markingDone = ref(false)

async function onToggleMarkDone() {
  if (!currentSessionId.value) return
  markingDone.value = true
  try {
    await putSessionLabeled(currentSessionId.value, !currentSessionLabeled.value)
    await refreshSessionsQuietly(true, props.projectName)
  } catch {
    // already surfaced via apiFetch
  } finally {
    markingDone.value = false
  }
}

// Some tabs aren't reactive to a selection change on their own — an
// explicit nudge here refreshes whichever one's active.
watch(selected, () => {
  nextTick(() => inspectorRef.value?.refresh())
})

onMounted(async () => {
  loadUsers()
  await loadSessions(true, props.projectName)
  const mostRecent = sessions.value[0] ?? null
  if (mostRecent) {
    selectSession(mostRecent) // ids necessarily differ here, so this always triggers watch(currentSessionId, loadTimeline)
  } else {
    // No sessions at all — watch() only fires on an actual change, so
    // a currentSessionId already null would never clear `loading`.
    loadTimeline()
  }
  window.addEventListener('resize', handleWindowResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleWindowResize)
})
</script>

<template>
  <div class="test-overlay">
    <div class="test-header">
      <button class="back-btn" title="Back" @click="emit('close')">«</button>
      <h2>Label sessions — {{ projectName }}</h2>
      <div class="test-header-actions">
        <ProjectsMenu :selected-name="projectName" @select="(name) => emit('project-select', name)" />
        <SettingsMenu
          :role="role"
          align="right"
          @manage-projects="emit('manage-projects')"
          @manage-users="emit('manage-users')"
          @label-sessions="emit('label-sessions')"
          @edit-projects="emit('edit-projects')"
          @about="emit('about')"
          @download-backup="emit('download-backup')"
          @restore-backup="(file) => emit('restore-backup', file)"
        />
        <ProfileMenu :profile="profile" @profile="emit('profile')" @logout="emit('logout')" />
      </div>
    </div>

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
                @updated="refreshSessionsQuietly(true, props.projectName)"
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
              :project-name="projectName"
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
              :project-name="projectName"
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
  /* Extends past the viewport's own bottom edge on standalone iOS,
     where WebKit bug #301108 leaves a gap there otherwise — see
     index.html's own viewport meta comment and
     useVisualViewport.js's installViewportOvershoot(). 0px, a no-op,
     everywhere else (a plain browser tab, non-iOS, or once Apple fixes
     the bug). */
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.test-header {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.test-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.test-header-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.test-header-actions .projects-menu {
  max-width: 220px;
}

.back-btn {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  padding: 0;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}

.back-btn:hover {
  background: #4a6fa5;
  color: white;
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

/* Collapsed (see SessionsPanel.vue's own always-visible header toggle) —
   a slim strip, same pattern as ChatWindow.vue's own equivalent. */
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

/* Same style as Inspector.vue's own .inspector-title. */
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

/* Collapsed (see Inspector.vue's own always-visible header toggle) —
   without this, width stayed pinned to --inspector-width regardless (the
   bug: an empty docked panel that never actually gave its own space back
   to the timeline/sessions split next to it). Same slim-strip convention
   EditProjectView.vue's own .inspector-panel-collapsed uses. */
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
