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
import CardMenu from '../../inspector/CardMenu.vue'
import { vAutosize } from '../../inspector/textareaAutosize.js'
import { handleEnterNext } from '../../inspector/enterToNextField.js'
import {
  getMessages, getSessionSignals, getSessions, getProjectGraph, postImportSessions,
  getExportSessions, deleteImportedSessions, putMessageExpectedState, putMessageExpectedSignals, putMessageComment,
  putSessionLabeled, putSessionTitle, putSessionComment, deleteSessionAnnotations, deleteSession, getUsers,
  putSessionsReassign, deleteTestUser, deleteUserSessions
} from '../../../api.js'
import { sessions, sessionsLoading, loadSessions, refreshSessionsQuietly } from '../../../chatStore.js'
import {
  buildTimeline, commentForMessage, highlightedStateKeyFor, signalValuesFor
} from '../../../testTimeline.js'
import { summarizeImportFailures } from '../../../sessionImport.js'
import { setApiError, clearApiError } from '../../../errorStore.js'
import { confirmDialog } from '../../../dialogStore.js'

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

const loading = ref(true)
// This view's own session pointer — never chatStore.js's shared
// currentSessionId. Browsing/reviewing a session here (including an
// imported one, which can never be "the" live session) must not leak
// into what the main chat window is showing.
const currentSessionId = ref(null)
// Raw backend message rows, kept as-is rather than chatStore.js's live
// `messages` shape — this view reviews a fixed past session, not a live
// conversation.
const rawMessages = ref([])
// The session's full Signals event log — timeline transitions,
// point-in-time signal reconstructions, and annotations are all derived
// from this alone, with no further backend round trips.
const signalsLog = ref([])
const sessionStartState = ref(null)

const inspectorRef = ref(null)
const inspectorWidth = ref(360)
const inspectorCollapsed = ref(false)
const inspectorTabs = computed(() => [
  { id: 'info', label: 'Info' },
  { id: 'states', label: 'States' },
  { id: 'signals', label: 'Signals' }
])
const inspectorActiveTab = ref('info')
// Starts open — reviewing a specific session is the point of this view.
const testSessionsPanelOpen = ref(true)
const sessionsPanelWidth = ref(240)
let dragTarget = null

function startInspectorDrag(event) {
  dragTarget = 'inspector'
  event.preventDefault()
}

function startSessionsDrag(event) {
  dragTarget = 'sessions'
  event.preventDefault()
}

function onDrag(event) {
  if (dragTarget === 'inspector') {
    inspectorWidth.value = Math.min(560, Math.max(240, inspectorWidth.value - event.movementX))
    inspectorRef.value?.resize()
  } else if (dragTarget === 'sessions') {
    sessionsPanelWidth.value = Math.min(420, Math.max(160, sessionsPanelWidth.value + event.movementX))
  }
}

function stopDrag() {
  dragTarget = null
}

// Independent of the main page's own Sessions panel state
// (chatStore.js's sessionsPanelOpen) — this overlay has its own panel.
function toggleTestSessionsPanel() {
  testSessionsPanelOpen.value = !testSessionsPanelOpen.value
  if (testSessionsPanelOpen.value) loadSessions(true, props.projectName)
}

// This view's Sessions panel reviews imported transcripts alongside live
// ones, so every load/refresh below passes includeImported.

// Every selected file (whichever mix of .txt transcripts and "Download
// all" .json exports) uploaded in one request — all per-file/per-session
// dispatch and error handling happens server-side; this just renders the
// returned result.
const importingSessions = ref(false)
// null until the first SSE progress chunk arrives — SessionsTree.vue
// shows the indeterminate spinner until then, a filling ring after.
const importProgress = ref(null)

async function handleImportSession(files) {
  importingSessions.value = true
  importProgress.value = null
  let result
  try {
    result = await postImportSessions(props.projectName, files, (message) => {
      importProgress.value = message.percentage
    })
  } catch {
    // already surfaced via apiFetch
    return
  } finally {
    importingSessions.value = false
    importProgress.value = null
  }

  if (result.last_session_id != null) {
    // The list must contain the new session before it can be looked up
    // in it — refresh first, select second, not the other way around.
    await refreshSessionsQuietly(true, props.projectName)
    const imported = sessions.value.find((s) => s.id === result.last_session_id)
    if (imported) selectSession(imported)
  }

  const failureSummary = summarizeImportFailures(result.results)
  if (failureSummary) setApiError(failureSummary.message, failureSummary.detail)
  else clearApiError()
}

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

async function onMoveSessions({ sessionIds, username }) {
  try {
    await putSessionsReassign(props.projectName, sessionIds, username)
    await refreshSessionsQuietly(true, props.projectName)
  } catch {
  }
}

async function onDeleteTestUser({ testUserSeq }) {
  const ok = await confirmDialog({
    title: 'Delete test user',
    body: `Delete Test User ${testUserSeq} and all of their sessions? This cannot be undone.`,
    okLabel: 'Delete',
    danger: true
  })
  if (!ok) return
  const deletedUsername = `Test user ${testUserSeq}`
  try {
    await deleteTestUser(props.projectName, testUserSeq)
    if (currentSession.value?.username === deletedUsername) currentSessionId.value = null
    await refreshSessionsQuietly(true, props.projectName)
  } catch {
  }
}

// Any other non-live branch's own × button (see SessionsTree.vue's
// isDeletableBranch) — an arbitrary imported username, not a "Test user N" one.
async function onDeleteUserSessions({ username }) {
  const ok = await confirmDialog({
    title: 'Delete sessions',
    body: `Delete every imported session from "${username}"? This cannot be undone.`,
    okLabel: 'Delete',
    danger: true
  })
  if (!ok) return
  try {
    await deleteUserSessions(props.projectName, username)
    if (currentSession.value?.username === username) currentSessionId.value = null
    await refreshSessionsQuietly(true, props.projectName)
  } catch {
  }
}

// The sessions panel's own "Delete all imported sessions" icon — every
// imported session of the project, across every user.
const deletingAllImported = ref(false)
async function handleDeleteAllImported() {
  const ok = await confirmDialog({
    title: 'Delete all imported sessions',
    body: 'Delete every imported session of this project? This cannot be undone.',
    okLabel: 'Delete',
    danger: true
  })
  if (!ok) return
  deletingAllImported.value = true
  try {
    await deleteImportedSessions(props.projectName)
    if (currentSessionIsImported.value) currentSessionId.value = null
    await refreshSessionsQuietly(true, props.projectName)
  } catch {
    // already surfaced via apiFetch
  } finally {
    deletingAllImported.value = false
  }
}

// Only an imported session is ever deletable here — a live/native one
// is the record of a real conversation, not this view's to discard.
const deletingSessionId = ref(null)
async function handleDeleteSession(session) {
  const ok = await confirmDialog({
    title: 'Delete session',
    body: `Delete this imported session (${session.title || session.end_state})? This cannot be undone.`,
    okLabel: 'Delete',
    danger: true
  })
  if (!ok) return
  deletingSessionId.value = session.id
  try {
    await deleteSession(session.id)
    if (session.id === currentSessionId.value) currentSessionId.value = null
    await refreshSessionsQuietly(true, props.projectName)
  } catch {
    // already surfaced via apiFetch
  } finally {
    deletingSessionId.value = null
  }
}

function handleWindowResize() {
  inspectorRef.value?.resize()
}

async function loadTimeline() {
  const sessionId = currentSessionId.value
  if (sessionId == null) {
    rawMessages.value = []
    signalsLog.value = []
    sessionStartState.value = null
    loading.value = false
    return
  }
  loading.value = true
  selected.value = null
  try {
    const [messageRows, signalRows, allSessions] = await Promise.all([
      getMessages(sessionId),
      getSessionSignals(sessionId),
      getSessions(props.projectName, true)
    ])
    rawMessages.value = messageRows
    signalsLog.value = signalRows
    sessionStartState.value = allSessions.find((s) => s.id === sessionId)?.start_state ?? null
    // Some tabs (e.g. States/Signals) don't reactively recompute on
    // session change alone, so this refreshes whichever one's active —
    // the `selected` reset above isn't enough when `selected` was
    // already null.
    await nextTick()
    inspectorRef.value?.refresh()
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

watch(currentSessionId, loadTimeline)

// Chronological, merged view of the session's messages and state
// transitions — real ones, plus any evaluation point an expert annotated
// even though nothing actually changed there. See testTimeline.js.
const timeline = computed(() =>
  buildTimeline(rawMessages.value, signalsLog.value, sessionStartState.value, { imported: currentSessionIsImported.value })
)

// The point in time currently reflected by the Inspector — a message or
// transition clicked in the timeline. null until the first click.
const selected = ref(null)

function selectMessage(message) {
  selected.value = { kind: 'message', message }
}

function selectTransition(transition) {
  selected.value = { kind: 'transition', transition }
}

// See testTimeline.js — avoids landing one point behind the
// current selection's own evaluation.
const highlightedStateKey = computed(() =>
  highlightedStateKeyFor(selected.value, timeline.value, sessionStartState.value)
)

// Only a transition has "the action that produced it" to highlight.
// old_state === '' (the automaton's init transition) is a real edge in
// the graph too (see InspectorGraphTab.vue's isInitEdge).
const firedActionEdge = computed(() => {
  if (selected.value?.kind !== 'transition') return null
  const t = selected.value.transition
  return { stateKey: t.old_state, actionName: t.action }
})

const signalValues = computed(() => signalValuesFor(selected.value, signalsLog.value, rawMessages.value))

// The currently-selected session's row out of the shared sessions list
// (chatStore.js). Null before the list has loaded, or if its id has
// since been deleted out from under it.
const currentSession = computed(() => sessions.value.find((s) => s.id === currentSessionId.value) ?? null)

// Whether the session currently being reviewed was imported rather than
// played live — the one case with no real Tracking rows for
// annotatableSignalsRow below to consult.
const currentSessionIsImported = computed(() => currentSession.value?.type === 'imported')

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

// Same fallback convention as SessionsPanel.vue's own
// formatSessionTimestamp, which isn't exported so is reimplemented here.
function formatSessionTimestamp(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

// An imported session has neither — no real conversation window to show,
// rather than a pair of em dashes implying one just wasn't recorded.
const currentSessionHasTimestamps = computed(() =>
  currentSession.value?.datetime_start != null || currentSession.value?.datetime_end != null
)

// The Signals row backing the current selection; null means no
// evaluation exists to annotate against. An imported session never has a
// real row, so a virtual placeholder stands in until the first write.
const annotatableSignalsRow = computed(() => {
  if (!selected.value) return null
  if (selected.value.kind === 'transition') {
    return selected.value.transition.message_id != null ? selected.value.transition : null
  }
  const message = selected.value.message
  const row = signalsLog.value.find((s) => s.message_id === message.id)
  if (row) return row
  if (currentSessionIsImported.value) {
    return { id: null, message_id: message.id, old_state: null, new_state: null, expected_state: null, expected_values: null, values: null }
  }
  return null
})

// The annotation API is message-centric, so a transition selection
// resolves back to whichever message its row says caused it.
const annotatableMessageId = computed(() => {
  if (!annotatableSignalsRow.value) return null
  return selected.value.kind === 'message' ? selected.value.message.id : annotatableSignalsRow.value.message_id
})

const expectedState = computed(() => annotatableSignalsRow.value?.expected_state ?? null)
const expectedValues = computed(() => {
  const raw = annotatableSignalsRow.value?.expected_values
  return raw ? JSON.parse(raw) : {}
})

// The automaton's starting point (old_state === "") has no real signal
// evaluation behind it — an expert can disagree about where the
// automaton starts, but never about signal values that don't exist.
const annotatableExpectedSignals = computed(() => {
  return annotatableSignalsRow.value != null && annotatableSignalsRow.value.old_state !== ''
})

// A full reload is needed because an annotation write can change which
// row exists for a message, not just its fields. Re-selects by
// message_id since the row's own id may have just changed.
async function reloadSignalsLog() {
  if (!currentSessionId.value) return
  signalsLog.value = await getSessionSignals(currentSessionId.value)
  if (selected.value?.kind === 'transition') {
    const messageId = selected.value.transition.message_id
    const match = timeline.value.find((e) => e.kind === 'transition' && e.transition.message_id === messageId)
    selected.value = match ? { kind: 'transition', transition: match.transition } : null
  }
  // The Sessions panel's has_annotations tag may have just flipped;
  // quiet, so it doesn't flash the panel to "Loading…" for a reload the
  // user never asked for.
  await refreshSessionsQuietly(true, props.projectName)
}

async function onUpdateExpectedState(value) {
  const messageId = annotatableMessageId.value
  if (messageId == null) return
  try {
    await putMessageExpectedState(messageId, value)
    await reloadSignalsLog()
    inspectorRef.value?.refresh()
  } catch {
    // already surfaced via apiFetch
  }
}

async function onUpdateExpectedSignals(values) {
  const messageId = annotatableMessageId.value
  if (messageId == null) return
  try {
    await putMessageExpectedSignals(messageId, values)
    await reloadSignalsLog()
    inspectorRef.value?.refresh()
  } catch {
    // already surfaced via apiFetch
  }
}

// Keyed directly off the clicked message's id rather than the current
// Inspector selection — the comment icon sits on every message row.
async function onSaveComment(messageId, comment) {
  try {
    await putMessageComment(messageId, comment)
    await reloadSignalsLog()
  } catch {
    // already surfaced via apiFetch
  }
}

// Whether this session has anything for "Unlabel all" to actually clear —
// disables the button rather than opening a confirm dialog for nothing.
const hasAnyAnnotations = computed(() => {
  return signalsLog.value.some((s) => s.expected_state != null || s.expected_values != null)
})

const unlabelingAll = ref(false)

async function onUnlabelAll() {
  if (!currentSessionId.value || !hasAnyAnnotations.value) return
  const ok = await confirmDialog({
    title: 'Remove annotations',
    body: 'Remove every annotation in this session? This cannot be undone.',
    okLabel: 'Remove',
    danger: true
  })
  if (!ok) return
  unlabelingAll.value = true
  try {
    await deleteSessionAnnotations(currentSessionId.value)
    await reloadSignalsLog()
    inspectorRef.value?.refresh()
  } catch {
    // already surfaced via apiFetch
  } finally {
    unlabelingAll.value = false
  }
}

// The persisted "reviewed" flag, read off the Sessions panel's list —
// unlike hasAnyAnnotations above, "is there anything to clear" is a
// different question than "has an expert signed off".
const currentSessionLabeled = computed(() => {
  return sessions.value.find((s) => s.id === currentSessionId.value)?.has_annotations ?? false
})

// Every session of this project as one .json file, re-uploadable through
// this view's own Import button. Same synthetic-<a> download trick as
// App.vue's handleModelDownload.
const downloadingSessions = ref(false)
// `type` ('live' | 'imported') comes from SessionsTree.vue's own active
// tab (see its 'download-all' emit) — Download all only ever exports
// whichever kind is currently showing.
async function handleDownloadSessions(type) {
  downloadingSessions.value = true
  try {
    const blob = await getExportSessions(props.projectName, type)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${props.projectName}-${type}-sessions.json`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch {
    // already surfaced via apiFetch
  } finally {
    downloadingSessions.value = false
  }
}

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

// The Info tab's Name/Comment fields — local buffers synced from
// currentSession on every session switch, committed on blur only if
// actually changed.
const editSessionTitle = ref('')
const editSessionComment = ref('')
watch(currentSession, (session) => {
  editSessionTitle.value = session?.title ?? ''
  editSessionComment.value = session?.comment ?? ''
}, { immediate: true })

// Click-to-open, click-to-close, same as InspectorSignalsTab.vue's own
// toggle. Collapses back on session switch so editing session A's
// name/comment never carries over into session B's still-open form.
const sessionInfoExpanded = ref(false)
const sessionNameInputRef = ref(null)
watch(currentSessionId, () => { sessionInfoExpanded.value = false })

async function toggleSessionInfo() {
  sessionInfoExpanded.value = !sessionInfoExpanded.value
  if (sessionInfoExpanded.value) {
    await nextTick()
    sessionNameInputRef.value?.focus()
  }
}

async function onUpdateSessionTitle() {
  const sessionId = currentSessionId.value
  if (!sessionId || editSessionTitle.value === (currentSession.value?.title ?? '')) return
  try {
    await putSessionTitle(sessionId, editSessionTitle.value)
    await refreshSessionsQuietly(true, props.projectName)
  } catch {
    // already surfaced via apiFetch
  }
}

async function onUpdateSessionComment() {
  const sessionId = currentSessionId.value
  if (!sessionId || editSessionComment.value === (currentSession.value?.comment ?? '')) return
  try {
    await putSessionComment(sessionId, editSessionComment.value)
    await refreshSessionsQuietly(true, props.projectName)
  } catch {
    // already surfaced via apiFetch
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
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', stopDrag)
  window.addEventListener('resize', handleWindowResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', stopDrag)
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
              <div
                class="inspector-signal-block inspector-signal-block-clickable"
                title="Click to open"
                @click="toggleSessionInfo"
              >
                <Transition name="crossfade" mode="out-in">
                  <div v-if="sessionInfoExpanded" key="edit" class="inspector-signal-form">
                    <div class="inspector-signal-header">
                      <span class="inspector-detail-badge inspector-detail-badge-session">Session</span>
                      <input
                        ref="sessionNameInputRef"
                        v-model="editSessionTitle"
                        class="inspector-signal-label-input"
                        placeholder="Untitled session"
                        @click.stop
                        @blur="onUpdateSessionTitle"
                        @keydown.enter.prevent="handleEnterNext"
                      />
                      <CardMenu v-if="currentSessionIsImported">
                        <button type="button" class="card-menu-item-danger" @click="handleDeleteSession(currentSession)">Delete</button>
                      </CardMenu>
                    </div>
                    <span v-if="currentSessionIsImported" class="inspector-detail-badge inspector-detail-badge-neutral">Imported</span>
                    <template v-if="currentSessionHasTimestamps">
                      <label class="inspector-signal-form-label">Started</label>
                      <p class="test-session-info-value">{{ formatSessionTimestamp(currentSession.datetime_start) }}</p>
                      <label class="inspector-signal-form-label">Ended</label>
                      <p class="test-session-info-value">{{ formatSessionTimestamp(currentSession.datetime_end) }}</p>
                    </template>
                    <label class="inspector-signal-form-label">Comment</label>
                    <textarea
                      v-model="editSessionComment"
                      v-autosize
                      class="inspector-signal-textarea"
                      rows="3"
                      placeholder="No comment yet."
                      @click.stop
                      @blur="onUpdateSessionComment"
                    ></textarea>
                  </div>
                  <div v-else key="readonly" class="inspector-signal-readonly">
                    <div class="inspector-signal-header">
                      <span class="inspector-detail-badge inspector-detail-badge-session">Session</span>
                      <span class="inspector-signal-name">{{ currentSession.title || currentSession.end_state || 'Untitled session' }}</span>
                      <CardMenu v-if="currentSessionIsImported">
                        <button type="button" class="card-menu-item-danger" @click="handleDeleteSession(currentSession)">Delete</button>
                      </CardMenu>
                    </div>
                    <span v-if="currentSessionIsImported" class="inspector-detail-badge inspector-detail-badge-neutral">Imported</span>
                    <span v-if="currentSession.comment" class="inspector-signal-ui_description">{{ currentSession.comment }}</span>
                  </div>
                </Transition>
              </div>
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
  inset: 0;
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

/* The session card itself — unified with InspectorSignalsTab.vue/
   InspectorEnvKeysTab.vue's own read-only/editable block (same classes,
   copied here since Vue's scoped styles never cross component files):
   a badge + title/name row, click to open into an editable form,
   CardMenu for Delete (imported sessions only — see currentSessionIsImported). */
.inspector-signal-block {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.6rem 0.75rem;
  border-radius: 8px;
  border: 1px solid #eee;
  background: #fafafa;
}

.inspector-signal-block-clickable {
  cursor: pointer;
}

.inspector-signal-block-clickable:hover {
  border-color: #c9d6e8;
  background: #f0f4fa;
}

.inspector-signal-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.inspector-detail-badge {
  flex-shrink: 0;
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  color: white;
}

.inspector-detail-badge-session {
  background: #455a64;
}

.inspector-detail-badge-neutral {
  background: #4a6fa5;
}

.inspector-signal-name {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
}

.inspector-signal-label-input {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
  border: 1px solid transparent;
  border-radius: 4px;
  padding: 0.1rem 0.3rem;
  background: transparent;
}

.inspector-signal-label-input:hover,
.inspector-signal-label-input:focus {
  border-color: #ccc;
  background: white;
}

.inspector-signal-form-label {
  display: block;
  margin: 20px 0 0.15rem;
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: #777;
}

.inspector-signal-textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  font: inherit;
  font-size: 0.78rem;
  line-height: 1.54;
  padding: 0.35rem 0.5rem;
  border-radius: 6px;
  border: 1px solid #ccc;
}

.inspector-signal-ui_description {
  display: block;
  margin-top: 0.3rem;
  font-size: 0.78rem;
  color: #666;
  line-height: 1.4;
}

.crossfade-enter-active,
.crossfade-leave-active {
  transition: opacity 0.15s ease;
}

.crossfade-enter-from,
.crossfade-leave-to {
  opacity: 0;
}
</style>
