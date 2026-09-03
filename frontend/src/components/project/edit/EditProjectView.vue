<script setup>
// Composes three mutually exclusive mode panels (ProjectDesignPanel/
// RunChat/ProjectTestPanel — see `mode` below) as siblings under
// .edit-project-panels; this view owns only what's cross-cutting: the
// mode switch, header/publish controls, dialogs, and the shared Inspector.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ProjectDesignPanel from './design/ProjectDesignPanel.vue'
import RunChat from './run/RunChat.vue'
import ProjectTestPanel from './test/ProjectTestPanel.vue'
import Inspector from '../../inspector/Inspector.vue'
import InspectorGraphTab from '../../inspector/InspectorGraphTab.vue'
import InspectorSignalsTab from '../../inspector/InspectorSignalsTab.vue'
import InspectorMetricsTab from '../../inspector/InspectorMetricsTab.vue'
import InspectorEnvTab from '../../inspector/InspectorEnvTab.vue'
import InspectorEnvKeysTab from '../../inspector/InspectorEnvKeysTab.vue'
import InspectorStateTab from '../../inspector/InspectorStateTab.vue'
import ActionsOrderDialog from '../../inspector/ActionsOrderDialog.vue'
import SessionDetailCard from '../../inspector/SessionDetailCard.vue'
import InspectorUserInfoCard from '../../inspector/InspectorUserInfoCard.vue'
import InspectorSignalDetailCard from '../../inspector/InspectorSignalDetailCard.vue'
import ModelMenu from '../../ModelMenu.vue'
import ProfileMenu from '../../ProfileMenu.vue'
import AppHeader from '../../AppHeader.vue'
import { useLeaveConfirmation } from '../../../composables/useLeaveConfirmation.js'
import { useResizablePanel } from '../../../composables/useResizablePanel.js'
import { useProjectFiles } from '../../../composables/useProjectFiles.js'
import { useProjectPublishing } from '../../../composables/useProjectPublishing.js'
import { useIndexYmlEditing } from '../../../composables/useIndexYmlEditing.js'
import { onProjectChanged } from '../../../projectChangeEvents.js'
import {
  clearProjectHistory,
  getSignals,
  getSessionSignals,
  getSessions,
  getMessages,
  getProjectGraph,
  getProjectEnvKeys,
  getProjectSignals,
  getStateInputTokens,
  getUsers
} from '../../../api.js'
import { setApiWarning } from '../../../errorStore.js'
import { confirmDialog, chooseDialog, customDialog } from '../../../dialogStore.js'
import { refreshIdentifierRegistry } from '../../../identifierRegistry.js'
import { refreshProjectFiles } from '../../../projectFiles.js'
import { buildTimeline, highlightedStateKeyFor, nearestMessageIdAtOrBefore, resultingStateKeyFor, signalValuesFor } from '../../../testTimeline.js'
// `sessions` here is the *project's* whole session catalog (loaded by
// ProjectTestPanel.vue, the "Test" tab, via the live store) —
// unrelated to runSessions below, "Run" mode's own draft session pool,
// which just happens to share the same name in testStore.
import { sessions, totalTokenBudgetPerSession } from '../../../chatStore.js'
import { activeChatMode } from '../../../chatSkin.js'
import { setTestProject, testStore, testChatModelStore, loadTestChatModels } from '../../../testChatStore.js'

// Aliased: this file already uses "state" for automaton state nodes.
// `runState` is the "Run" tab's own current conversation state,
// highlighted as "current" in the Inspector (see highlightedStateKey
// below) only while that tab is actually open.
const {
  state: runState,
  messages,
  currentSessionId,
  draft,
  turnCount,
  handleSend,
  handleTruncateFrom,
  loadMessages,
  loadSessions,
  sessions: runSessions,
  refreshSessionsQuietly
} = testStore

const props = defineProps({
  projectName: {
    type: String,
    required: true
  },
  // ProfileMenu.vue's own avatar/name — App.vue already fetched this once
  // during boot, passed straight through so this view can show the same
  // topbar avatar the main chat screen does.
  profile: { type: Object, default: null }
})

// One EditProjectView instance is always scoped to a single project for
// its whole lifetime — the "Run" tab's own test store targets it once here.
setTestProject(props.projectName)

const emit = defineEmits(['saved', 'back', 'profile', 'logout'])

const {
  filesLoading, files, currentFileName, justAddedFileName, uploading, creatingFile, deletingFile, renamingFile,
  designPanelRef, codeEditorRef, indexYmlEditorRef, indexCssEditorRef, mdEditorRef,
  currentFileIsImage, currentFileIsMarkdown, isBehaviorNodeSelected, hasTheme,
  activeEditorIsDirty, activeEditor,
  loadFiles, switchFile, guardedAction, selectFile, jumpToDefinition,
  handleUploadFile, handleNewAttachment, handleNewAspect, handleNewLegal, handleDeleteFile, handleRenameFile,
  handleFileRenamedByHistory, handleFileSaved,
} = useProjectFiles(props.projectName, emit)

// Inspect panel: the shared Inspector component shows the last-saved
// project's state graph, signal definitions, and metrics_framework's core
// metrics. `inspectorRef` drives reload-after-save, Metrics refresh, and graph resize on drag.
const inspecting = ref(true)
const inspectorRef = ref(null)
const { width: inspectorWidth, startDrag: startInspectorDrag } = useResizablePanel(360, {
  min: 240, max: 560, invert: true, onResize: () => inspectorRef.value?.resize()
})
// The Graph/State-tab/Actions-tab shared selection ({kind, data} | null),
// same shape (see InspectorGraph.vue's 'select' emit) whether it came from
// index.yml's own dedicated graph or the Inspector's "States" tab.
const selectedGraphElement = ref(null)

// Identifies whatever a state/action/signal add-button just created — a
// matching card plays a yellow-fade highlight (see InspectorDetailCard.
// vue's elementIdentity, e.g. 'state:<key>', 'signal:<name>').
const recentlyAddedKey = ref(null)
const RECENTLY_ADDED_FLASH_MS = 1600
let recentlyAddedTimer = null
function flashRecentlyAdded(key) {
  recentlyAddedKey.value = key
  if (recentlyAddedTimer) clearTimeout(recentlyAddedTimer)
  recentlyAddedTimer = setTimeout(() => { recentlyAddedKey.value = null }, RECENTLY_ADDED_FLASH_MS)
}
onBeforeUnmount(() => { if (recentlyAddedTimer) clearTimeout(recentlyAddedTimer) })

// The state key "State"/"Actions" should reflect — the selection itself
// when it's already a state, or the state an already-selected action
// belongs to (see InspectorGraph.vue's edgeToCyData: matchStateKey).
const selectedStateKey = computed(() => {
  if (!selectedGraphElement.value) return null
  return selectedGraphElement.value.kind === 'state'
    ? selectedGraphElement.value.data.id
    : selectedGraphElement.value.data.matchStateKey
})

// Run mode's own currently selected test session, from the same shared
// list its Session Explorer (RunChat.vue) loads — read here for the
// Inspector's SessionDetailCard, which RunChat.vue doesn't itself show.
const runCurrentSession = computed(() => runSessions.value.find((s) => s.id === currentSessionId.value) ?? null)

// Test mode's own selection (ProjectTestPanel.vue's own selectedNodeId —
// this view only ever gets told what it is via @select, never owns the
// canonical value) — 'root' | 'sessions-branch' | 'states-branch' |
// `session:<id>` | `state:<key>` | null. Drives the Inspector's Info tab
// below, read-only, in place of the Graph selection edit mode uses.
const autoSelectedNodeId = ref(null)
function handleAutoSelect(nodeId) { autoSelectedNodeId.value = nodeId }

const autoSelectedSessionId = computed(() => {
  const id = autoSelectedNodeId.value
  return id && id.startsWith('session:') ? Number(id.slice('session:'.length)) : null
})
// From chatStore.js's already-loaded list — ProjectTestPanel.vue's own
// onMounted triggers that load, so it's there by the time anything here can be selected.
const autoSelectedSession = computed(() => {
  const id = autoSelectedSessionId.value
  return id == null ? null : (sessions.value.find((s) => s.id === id) ?? null)
})
const autoSelectedStateKey = computed(() => {
  const id = autoSelectedNodeId.value
  return id && id.startsWith('state:') ? id.slice('state:'.length) : null
})

// The Test tree's "Users" branch — a user node directly, or any session
// leaf (its own `username`, now that sessions carry one — see db/sessions'
// own username plumbing). Resolved against usersList (loaded lazily, see
// ensureUsersList) for the same read-only profile card ManageUsersView.vue
// shows (see InspectorUserInfoCard.vue).
const usersList = ref([])
let usersListLoaded = false
async function ensureUsersList() {
  if (usersListLoaded) return
  usersListLoaded = true
  try {
    usersList.value = (await getUsers()).users
  } catch {
    // already surfaced via apiFetch
  }
}
const autoSelectedUsername = computed(() => {
  const id = autoSelectedNodeId.value
  if (id && id.startsWith('user:')) return id.slice('user:'.length)
  return autoSelectedSession.value?.username ?? null
})
const autoSelectedUser = computed(() => {
  const username = autoSelectedUsername.value
  return username == null ? null : (usersList.value.find((u) => u.email === username || u.id === username) ?? null)
})
const autoSelectedElement = computed(() => {
  const key = autoSelectedStateKey.value
  return key == null ? null : (indexYmlEditorRef.value?.stateElementFor(key) ?? null)
})

// The Test tree's per-signal leaves — resolved against signalsList (loaded
// lazily, same shape as usersList above) for the read-only detail card
// (see InspectorSignalDetailCard.vue).
const signalsList = ref([])
let signalsListLoaded = false
async function ensureSignalsList() {
  if (signalsListLoaded) return
  signalsListLoaded = true
  try {
    signalsList.value = (await getProjectSignals(props.projectName, null, null)).signals
  } catch {
    // already surfaced via apiFetch
  }
}
const autoSelectedSignalName = computed(() => {
  const id = autoSelectedNodeId.value
  return id && id.startsWith('signal:') ? id.slice('signal:'.length) : null
})
const autoSelectedSignal = computed(() => {
  const name = autoSelectedSignalName.value
  return name == null ? null : (signalsList.value.find((s) => s.signal.name === name)?.signal ?? null)
})

// A selected session's own start/end state — same resolution as
// LabelProjectView.vue's own Info tab (see its sessionStartStateKey/
// sessionEndStateKey docstring): an imported session never actually ran
// against the automaton, so its first/last expert-annotated expected_state
// stands in for start_state/end_state.
const autoSessionSignals = ref([])
watch(autoSelectedSessionId, async (id) => {
  autoSessionSignals.value = id == null ? [] : await getSessionSignals(id).catch(() => [])
})

const autoSessionMessages = ref([])
watch(autoSelectedSessionId, async (id) => {
  autoSessionMessages.value = id == null ? [] : await getMessages(id).catch(() => [])
})
// FIXME: null (bar hidden), not 0, when no message ever reported tokens.
const autoSessionInputTokens = computed(() => {
  const userMessages = autoSessionMessages.value.filter((m) => m.role === 'user')
  if (!userMessages.some((m) => m.tokens != null)) return null
  return userMessages.reduce((sum, m) => sum + (m.tokens ?? 0), 0)
})
const autoSessionIsImported = computed(() => autoSelectedSession.value?.type === 'imported')
const autoSessionAnnotatedStates = computed(() => autoSessionSignals.value.map((row) => row.expected_state).filter(Boolean))
const autoSessionStartStateKey = computed(() => (
  autoSessionIsImported.value ? (autoSessionAnnotatedStates.value[0] ?? null) : (autoSelectedSession.value?.start_state ?? null)
))
const autoSessionEndStateKey = computed(() => (
  autoSessionIsImported.value ? (autoSessionAnnotatedStates.value.at(-1) ?? null) : (autoSelectedSession.value?.end_state ?? null)
))
const autoSessionStartElement = computed(() => (
  autoSessionStartStateKey.value == null ? null : (indexYmlEditorRef.value?.stateElementFor(autoSessionStartStateKey.value) ?? null)
))
const autoSessionEndElement = computed(() => (
  autoSessionEndStateKey.value == null ? null : (indexYmlEditorRef.value?.stateElementFor(autoSessionEndStateKey.value) ?? null)
))

// The tab set this view's Inspector shows (see Inspector.vue's slot-based
// contract; LabelProjectView.vue passes a different set). 'run' mode
// shows the live conversation's Metrics/Env; edit mode shows index.yml's own
// Info/Signals/Env-keys instead — Info doubles as the Actions tab used to,
// showing whichever of a state/action the Graph selection actually is.
// Test mode only ever shows Info — plain read-only viewing, no Signals/
// Env-keys editing surface makes sense while browsing test results.
const inspectorTabs = computed(() => {
  if (mode.value === 'run') {
    return [
      { id: 'states', label: 'Info' },
      { id: 'signals', label: 'Signals' },
      { id: 'metrics', label: 'Metrics' },
      { id: 'env', label: 'Env' }
    ]
  }
  if (mode.value === 'test') {
    return [{ id: 'state', label: 'Info' }, { id: 'user', label: 'User' }]
  }
  if (mode.value === 'edit' && !isBehaviorNodeSelected.value) {
    return [{ id: 'state', label: 'Info' }]
  }
  return [
    { id: 'state', label: 'Info' },
    { id: 'signals', label: 'Signals' },
    { id: 'env-keys', label: 'Env' }
  ]
})
const inspectorActiveTab = ref('states')

// Live value/error per signal name — fed to the Inspector's signal-values
// prop, refreshed on its own cadence (see refreshSignalValues).
const signalValueByName = ref({})

// The live session's Signals event log and starting state — feeds the
// same clickable message+transition timeline as LabelProjectView.vue's
// review one (see ChatTimeline.vue/testTimeline.js), just kept live.
const signalsLog = ref([])
const sessionStartState = ref(null)

// The point in time the Inspector reflects — null means "follow the live
// conversation", a value means "pinned to whatever was clicked in the
// timeline" (see selectMessage/selectTransition, both a toggle).
const selected = ref(null)

// testStore's own `messages` (the "Run" tab's draft conversation)
// reshaped into the common input shape buildTimeline expects. The
// in-flight assistant placeholder has no messageId yet — kept in with
// `id: null`, unmatched until it resolves.
const rawLiveMessages = computed(() =>
  messages.value.map((m) => ({
    ...m,
    id: m.messageId ?? null,
    audio_text: m.audioText
  }))
)

// includeSelfLoops: true — the live chat should show every action that
// actually fired, including a self-loop that left the state unchanged
// (see ChatTimeline.vue's own dimmed styling for these).
const timeline = computed(() =>
  buildTimeline(rawLiveMessages.value, signalsLog.value, sessionStartState.value, { includeSelfLoops: true })
)

async function refreshSignalsLog() {
  if (!currentSessionId.value) {
    signalsLog.value = []
    return
  }
  try {
    signalsLog.value = await getSessionSignals(currentSessionId.value)
  } catch {
    // already surfaced via apiFetch
  }
}

async function refreshSessionStartState() {
  if (!currentSessionId.value) {
    sessionStartState.value = null
    return
  }
  try {
    const allSessions = await getSessions(props.projectName)
    sessionStartState.value = allSessions.find((s) => s.id === currentSessionId.value)?.start_state ?? null
  } catch {
    // already surfaced via apiFetch
  }
}

// The project's current set of real state keys (nodes only — the
// reserved "" implicit state is never one of these). Backs isStateGone
// below: a bubble whose state has been renamed/removed has nowhere to land.
const validStateKeys = ref(new Set())

// Every real state's {key, uiLabel} — the Actions tab's target <select>
// options (see InspectorDetailCard.vue's availableStates prop).
const availableStates = ref([])

// Every declared project env key's name — the Actions tab's Env editor
// <select> options (see InspectorDetailCard.vue's availableEnvKeys prop).
const availableEnvKeys = ref([])

// The live chat's timeline (see ChatTimeline.vue's resolveStateLabel
// prop) shows a transition's ui-label instead of its raw state key.
// Falls back to the raw key for a state that's since been renamed/removed (see isStateGone).
function stateLabelFor(key) {
  return availableStates.value.find((s) => s.key === key)?.uiLabel ?? key
}

// Action name -> ui-label, keyed by `${stateKey}::${actionName}` — an
// action's name is only unique *within* its declaring state, so
// resolving by name alone could pick up the wrong state's same-named action.
const actionLabelsByState = ref(new Map())

function actionLabelFor(stateKey, actionName) {
  return actionLabelsByState.value.get(`${stateKey}::${actionName}`) ?? actionName
}

async function refreshValidStateKeys() {
  try {
    const { nodes, edges } = await getProjectGraph(props.projectName)
    validStateKeys.value = new Set(nodes.map((n) => n.state.key))
    availableStates.value = nodes.map((n) => ({ key: n.state.key, uiLabel: n.state.ui_label }))
    actionLabelsByState.value = new Map(
      edges.map((e) => [`${e.source}::${e.action.name}`, e.action.ui_label])
    )
  } catch {
    // already surfaced via apiFetch
  }
  try {
    availableEnvKeys.value = (await getProjectEnvKeys(props.projectName)).env_keys.map((e) => e.env_key.name)
  } catch {
    // already surfaced via apiFetch
  }
  // Common point every project edit funnels through — refreshing the
  // shared trigger-autocomplete registry here (see identifierRegistry.js)
  // covers every way a signal/action can come into existence.
  refreshIdentifierRegistry(props.projectName)
  refreshProjectFiles(props.projectName)
}

// The state a given message's turn left the conversation in — a
// different question than highlightedStateKeyFor's (see resultingStateKeyFor).
function isStateGone(message) {
  const stateKey = resultingStateKeyFor({ kind: 'message', message }, timeline.value, sessionStartState.value)
  return stateKey != null && !validStateKeys.value.has(stateKey)
}

function selectMessage(message) {
  selected.value =
    selected.value?.kind === 'message' && selected.value.message.id === message.id
      ? null
      : { kind: 'message', message }
}

function selectTransition(transition) {
  selected.value =
    selected.value?.kind === 'transition' && selected.value.transition.id === transition.id
      ? null
      : { kind: 'transition', transition }
}

// Falls back to the Run tab's own current state/signals (rather than
// null) whenever nothing is selected. "Current state" only means
// anything in 'run' mode — 'edit' has no conversation driving the graph,
// so nothing is ever "current" while editing.
const highlightedStateKey = computed(() => {
  if (mode.value !== 'run') return null
  return selected.value ? highlightedStateKeyFor(selected.value, timeline.value, sessionStartState.value) : (runState.value?.key ?? null)
})

// old_state === '' (the init transition) is a real, clickable edge in
// the graph too (see InspectorGraphTab.vue's isInitEdge) — every
// transition selection highlights *some* edge.
const firedActionEdge = computed(() => {
  if (selected.value?.kind !== 'transition') return null
  const t = selected.value.transition
  return { stateKey: t.old_state, actionName: t.action }
})

const untilMessageId = computed(() => {
  if (!selected.value) return null
  if (selected.value.kind === 'message') return selected.value.message.id
  return (
    selected.value.transition.message_id ??
    nearestMessageIdAtOrBefore(rawLiveMessages.value, selected.value.transition.timestamp)
  )
})

// The Env tab's "is this still 'now'?": true with nothing selected, and
// also true when the selected bubble is the conversation's latest
// message — nothing happened after it yet.
const latestMessageId = computed(() => {
  const msgs = rawLiveMessages.value
  return msgs.length ? msgs[msgs.length - 1].id : null
})
const envEditable = computed(() =>
  !selected.value ||
  (selected.value.kind === 'message' && selected.value.message.id === latestMessageId.value)
)

const effectiveSignalValues = computed(() =>
  selected.value ? signalValuesFor(selected.value, signalsLog.value, rawLiveMessages.value) : signalValueByName.value
)

// "Restart from here": both truncate the conversation at this message's
// timestamp (see testStore's own handleTruncateFrom, which rolls its
// state back too), then differ in what happens to the text — preloaded, or resent as-is.
const runChatRef = ref(null)

async function restartAndPrefill(message) {
  await handleTruncateFrom(message.timestamp)
  selected.value = null
  draft.value = message.content
  await nextTick()
  runChatRef.value?.focus()
}

async function restartAndResend(message) {
  await handleTruncateFrom(message.timestamp)
  selected.value = null
  await handleSend(message.content)
}

// Single "Design | Run | Test" segmented control: 'edit' shows the file
// explorer + editor, 'run' shows the embedded chat, 'test' shows the
// replay tree. Also gates the Inspector's tab set (see inspectorTabs).
const mode = ref('edit')
const editorOpen = computed(() => mode.value === 'edit')
const runOpen = computed(() => mode.value === 'run')
const testOpen = computed(() => mode.value === 'test')

// Whichever state key the "State" Inspector tab is actually showing right
// now — selectedGraphElement's own containing state in edit mode,
// autoSelectedElement's own key while browsing a run (see InspectorStateTab's
// :selected-element binding below).
const stateTabTokensKey = computed(() => (mode.value === 'test' ? autoSelectedStateKey.value : selectedStateKey.value))

// Estimated input-token cost of that state's own turn prompt (see backend
// ProjectInspector.get_state_input_tokens) — null while unknown/loading,
// or when nothing is selected. Fetched on demand per state rather than
// bundled into the Graph fetch, since it can cost a real provider call.
const stateTabTokens = ref(null)
let stateTabTokensRequestId = 0

async function refreshStateTabTokens() {
  const key = stateTabTokensKey.value
  if (!key) {
    stateTabTokens.value = null
    return
  }
  const requestId = ++stateTabTokensRequestId
  try {
    const { tokens } = await getStateInputTokens(props.projectName, key)
    if (requestId === stateTabTokensRequestId) stateTabTokens.value = tokens
  } catch {
    // already surfaced via apiFetch
    if (requestId === stateTabTokensRequestId) stateTabTokens.value = null
  }
}

watch(stateTabTokensKey, refreshStateTabTokens, { immediate: true })

// Entering 'run' mode bootstraps a chat session against the draft, even
// if a real native session is already active — testStore is its own
// independent chat (see testChatStore.js), so its currentSessionId
// naturally survives a Design/Test <-> Run switch on its own; no
// remember-and-restore hack needed.
async function ensureDraftChatSession() {
  await loadMessages()
  await loadSessions()
}

function setMode(next) {
  mode.value = next
  // Only chatSkin.js's own skin routing reads this — see its own docstring.
  activeChatMode.value = next === 'run' ? 'test' : 'live'
  if (next === 'run') ensureDraftChatSession()
  if (next === 'test') {
    ensureUsersList()
    ensureSignalsList()
  }
}

onBeforeUnmount(() => { activeChatMode.value = 'live' })

const { width: explorerWidth, startDrag: startExplorerDrag } = useResizablePanel(220, { min: 160, max: 420 })

async function refreshAfterProjectEdit() {
  await indexYmlEditorRef.value?.refresh(false)
  await indexYmlEditorRef.value?.reloadCode()
  if (inspecting.value) await inspectorRef.value?.refresh()
  refreshValidStateKeys()
  refreshProjectRevision()
  refreshStateTabTokens()
}

const unsubscribeProjectChanged = onProjectChanged((changedProjectName) => {
  if (changedProjectName === props.projectName) return refreshAfterProjectEdit()
})
onBeforeUnmount(unsubscribeProjectChanged)

const {
  projectRevision, publishing, publishRemapPrompt, publishRemapChoice, pendingLeaveAction,
  refreshProjectRevision, publishUpToDate,
  handlePublish, confirmPublishRemap, cancelPublishRemap,
  canRevert, publishMenuOpen, closePublishMenu, handleRevert,
} = useProjectPublishing(props.projectName, currentFileName, activeEditor, selectedGraphElement)

const {
  handleAddState, handleAddSignal, handleAddEnvKey, handleAddAction,
  handleSetStateField, handleSetProjectField, handleSetActionField, handleSetSignalField, handleSetEnvKeyField,
  handleDeleteState, handleDeleteAction, handleDeleteSignal, handleDeleteEnvKey,
} = useIndexYmlEditing(
  props.projectName, guardedAction, indexYmlEditorRef, jumpToDefinition, selectedGraphElement, selectedStateKey, flashRecentlyAdded
)

// The Inspector's "Info" tab shares the same selection the Graph drives,
// but a row click here only emits 'select' — this is the tab-side
// equivalent of a Graph click's select + jump-to-definition. Silent:
// shouldn't yank the user into the Code segment.
function handleTabSelect(element) {
  selectedGraphElement.value = element
  if (!element) return
  if (element.kind === 'state') jumpToDefinition({ kind: 'state', stateKey: element.data.id }, { silent: true })
  else jumpToDefinition(
    { kind: 'action', stateKey: element.data.matchStateKey, actionName: element.data.actionName },
    { silent: true }
  )
}

// The Info tab's detail card is generic (state or action, whichever is
// selected) — these two dispatch a field-edit/delete to whichever of
// handleSetStateField/handleSetActionField or handleDeleteState/
// handleDeleteAction actually applies, off selectedGraphElement's own kind.
function handleSetSelectedElementField(field, value) {
  const element = selectedGraphElement.value
  if (!element) return
  if (element.kind === 'state') handleSetStateField(element.data.id, field, value)
  else handleSetActionField(element.data.matchStateKey, element.data.actionName, field, value)
}

function handleDeleteSelectedElement(element) {
  if (!element) return
  if (element.kind === 'state') handleDeleteState(element.data.id)
  else handleDeleteAction(element.data.matchStateKey, element.data.actionName)
}

function handleOpenActionsOrder(element) {
  if (element?.kind !== 'state') return
  const stateKey = element.data.id
  guardedAction('reorder actions', () => {
    customDialog({
      component: ActionsOrderDialog,
      props: {
        projectName: props.projectName,
        stateName: stateKey,
        actions: indexYmlEditorRef.value?.actionsForState(stateKey) ?? []
      }
    })
  })
}

// The state edit form's attachment buttons (see InspectorDetailCard.vue's
// selectAttachment) jump to where the file is declared in index.yml
// rather than opening it, unlike every other attachment button elsewhere.
// selectAttachment only ever emits this for a state selection (an action's
// own attachments go through select-attachment instead), so selectedGraphElement
// is guaranteed to be the state kind whenever this actually fires.
function handleJumpToAttachment(fileName) {
  const stateKey = selectedGraphElement.value?.data.id
  if (stateKey == null) return
  jumpToDefinition({ kind: 'attachment', stateKey, fileName }, { silent: true })
}

// Only prompts when there's actually something to lose — a clean editor
// (nothing typed, or already saved) closes straight away. Undo/redo
// history itself is cleared on entry, not here — see onMounted.
const { confirmLeaveIfNeeded } = useLeaveConfirmation(activeEditorIsDirty, 'Discard unsaved changes to this file?')

// Unsaved-file changes are checked first — the more urgent, data-loss
// concern. Only past that, and only when there's actually a pending
// revision to decide about, does a three-way choice show: publish before
// leaving, leave it pending, or cancel the close outright. Shared by
// every Settings-menu item that actually navigates away from here (see
// handleSettings* below) — About/Download backup/Restore backup don't
// navigate away, so they skip this guard entirely.
async function leaveEditProject(onLeave) {
  if (!(await confirmLeaveIfNeeded())) return
  if (publishUpToDate.value) {
    onLeave()
    return
  }
  const choice = await chooseDialog({
    title: 'Unpublished changes',
    body: `Revision ${projectRevision.value?.revision} isn't published yet.`,
    options: [
      { id: 'publish', label: 'Publish and close' },
      { id: 'leave', label: 'Leave pending' }
    ]
  })
  if (choice === 'publish') {
    pendingLeaveAction.value = onLeave
    handlePublish()
    return
  }
  if (choice === 'leave') onLeave()
  // null (Cancel/backdrop/ESC) — stay open, nothing to do.
}

function handleBack() {
  leaveEditProject(() => emit('back'))
}

// Live values for whatever signals the active conversation currently
// has. Just the values — the flash-on-change animation is Inspector.vue's own concern.
async function refreshSignalValues() {
  try {
    const nextValues = await getSignals()
    signalValueByName.value = Object.fromEntries(nextValues.map((s) => [s.name, { value: s.value, error: s.error }]))
  } catch {
    // already surfaced via apiFetch
  }
}

// Shared by the initial mount (Inspect is open by default) and every
// later re-expand (see handleInspectorCollapsedChange). Inspector.vue
// loads its own graph/signals definitions on mount — this view only owns the live/point-in-time pieces layered on top.
async function openInspect() {
  await nextTick()
  await refreshSignalValues()
}

// Inspector.vue's own collapse toggle (see its own header) drives this.
function handleInspectorCollapsedChange(collapsed) {
  inspecting.value = !collapsed
  if (inspecting.value) openInspect()
}

// The graph box's own size changes with the inspector panel's width (drag)
// and with the viewport (narrow-screen full-takeover breakpoint, window
// resize) — Cytoscape needs an explicit nudge to notice either.
function handleWindowResize() {
  inspectorRef.value?.resize()
}

// A turn can shift signal values enough to change which action would fire
// next even without a state change. Metrics are heavier, so they only
// refresh while the Metrics tab is open (see InspectorMetricsTab.vue's refresh(active)).
watch(turnCount, () => {
  // A completed turn always adds a new message — the Inspector should
  // follow the conversation's newest message again, not stay pinned on a
  // bubble that's no longer the latest.
  selected.value = null
  refreshSignalsLog()
  if (!inspecting.value) return
  refreshSignalValues()
  inspectorRef.value?.refresh()
  // index.yml's own dedicated graph needs the same nudge while it's the
  // one showing (editorOpen on) — the Inspector's "States" tab doesn't exist then (see inspectorTabs).
  if (editorOpen.value) indexYmlEditorRef.value?.refresh(false)
})

// Metrics and Env aren't reactive to a prop change on their own — a
// selection change needs its own explicit nudge (see InspectorMetricsTab.
// vue's refresh(active) / InspectorEnvTab.vue's loadEnv).
watch(selected, () => {
  if (!inspecting.value) return
  nextTick(() => {
    inspectorRef.value?.refresh()
  })
})

// A session switch (testStore's own selectSession) always shows *that*
// session's timeline from scratch.
watch(currentSessionId, () => {
  selected.value = null
  refreshSessionStartState()
  refreshSignalsLog()
})

// Gates mounting CodeEditor/IndexYmlEditorPanel (passed as history-cleared)
// — without this, either could load before clearProjectHistory finishes,
// leaving their first can_undo/can_redo reflecting pre-clear history.
const historyCleared = ref(false)

onMounted(async () => {
  loadFiles()
  loadTestChatModels()
  refreshSessionStartState()
  refreshSignalsLog()
  refreshValidStateKeys()
  await refreshProjectRevision()
  // Surfaced once, right on entry — not re-triggered by later
  // refreshProjectRevision calls, or it would pop back open after dismissal.
  if (projectRevision.value?.is_paused) {
    setApiWarning(projectRevision.value.paused_reason || `Project '${props.projectName}' is currently paused.`)
  }
  if (inspecting.value) openInspect()
  window.addEventListener('resize', handleWindowResize)
  // A fresh editing session starts with a clean undo/redo slate — cleared
  // here (entry), not on Back.
  try {
    await clearProjectHistory(props.projectName)
  } catch {
    // already surfaced via apiFetch — the session still opens either way
  } finally {
    historyCleared.value = true
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleWindowResize)
})
</script>

<template>
  <div class="edit-project-overlay">
    <AppHeader>
      <template #left>
        <button class="app-header-icon-btn" title="Back" @click="handleBack">«</button>
        <ModelMenu :model-store="testChatModelStore" />
      </template>
      <template #center>
        <div class="mode-segment">
          <button
            class="mode-segment-btn"
            :class="{ 'mode-segment-btn-active': mode === 'edit' }"
            @click="setMode('edit')"
          >Design</button>
          <button
            class="mode-segment-btn"
            :class="{ 'mode-segment-btn-active': mode === 'run' }"
            @click="setMode('run')"
          >Run</button>
          <button
            class="mode-segment-btn"
            :class="{ 'mode-segment-btn-active': mode === 'test' }"
            @click="setMode('test')"
          >Test</button>
        </div>
      </template>
      <template #right>
        <div class="edit-project-header-actions">
          <div v-if="projectRevision" class="publish-split-btn">
            <button
              class="publish-btn"
              :disabled="publishUpToDate || publishing"
              :title="`Draft revision ${projectRevision.revision} — published: ${projectRevision.published_revision ?? 'never'}`"
              @click="handlePublish"
            >{{ publishing ? 'Publishing…' : `Rev. ${projectRevision.revision}` }}</button>
            <template v-if="canRevert">
              <button
                type="button"
                class="publish-menu-toggle"
                title="More publish options"
                @click="publishMenuOpen = !publishMenuOpen"
              >▾</button>
              <div v-if="publishMenuOpen" class="publish-menu-dropdown">
                <button type="button" class="publish-menu-item" @click="closePublishMenu(); handlePublish()">Publish</button>
                <button
                  type="button"
                  class="publish-menu-item publish-menu-item-danger"
                  @click="closePublishMenu(); handleRevert()"
                >Revert to rev. {{ projectRevision.published_revision }}</button>
              </div>
            </template>
          </div>
          <ProfileMenu :profile="profile" @profile="emit('profile')" @logout="emit('logout')" />
        </div>
      </template>
    </AppHeader>

    <div class="edit-project-body">
      <div class="edit-project-panels">
        <ProjectDesignPanel
          v-show="editorOpen"
          ref="designPanelRef"
          :project-name="projectName"
          :files="files"
          :files-loading="filesLoading"
          :current-file-name="currentFileName"
          :just-added-file-name="justAddedFileName"
          :uploading="uploading"
          :creating-file="creatingFile"
          :explorer-width="explorerWidth"
          :history-cleared="historyCleared"
          :current-file-is-image="currentFileIsImage"
          :current-file-is-markdown="currentFileIsMarkdown"
          :highlighted-state-key="highlightedStateKey"
          :fired-action-edge="firedActionEdge"
          :selected-element="selectedGraphElement"
          @start-explorer-drag="startExplorerDrag"
          @new-attachment="handleNewAttachment"
          @new-aspect="handleNewAspect"
          @new-legal="handleNewLegal"
          @select-file="selectFile"
          @upload-file="handleUploadFile"
          @jump-to-definition="(target) => jumpToDefinition(target, { silent: true })"
          @select="selectedGraphElement = $event"
          @saved="handleFileSaved"
          @renamed="handleFileRenamedByHistory"
        />

        <Transition name="panel-slide-bottom">
          <RunChat
            v-if="runOpen"
            ref="runChatRef"
            :timeline="timeline"
            :signals-log="signalsLog"
            :selected="selected"
            :has-theme="hasTheme"
            :resolve-state-label="stateLabelFor"
            :resolve-action-label="actionLabelFor"
            :is-state-gone="isStateGone"
            @select-message="selectMessage"
            @select-transition="selectTransition"
            @restart-prefill="restartAndPrefill"
            @restart-resend="restartAndResend"
          />
        </Transition>

        <ProjectTestPanel v-if="testOpen" :project-name="projectName" @select="handleAutoSelect" />
      </div>

      <div class="inspector-wrap">
        <div v-if="inspecting" class="split-divider inspector-divider" @mousedown="startInspectorDrag"></div>

        <div class="inspector-panel" :class="{ 'inspector-panel-collapsed': !inspecting }" :style="inspecting ? { '--inspector-width': inspectorWidth + 'px' } : null">
          <Inspector
            ref="inspectorRef"
            :tabs="inspectorTabs"
            v-model:active-tab="inspectorActiveTab"
            :collapsed="!inspecting"
            @update:collapsed="handleInspectorCollapsedChange"
          >
            <template #tab-states="{ registerTab }">
              <SessionDetailCard
                v-if="runCurrentSession"
                :session="runCurrentSession"
                @updated="refreshSessionsQuietly"
              />
              <InspectorGraphTab
                :ref="registerTab('states')"
                :project-name="projectName"
                :highlighted-state-key="highlightedStateKey"
                :auto-jump-on-highlight-change="true"
                :fired-action-edge="firedActionEdge"
                :editable-files="files"
                @jump-to-definition="jumpToDefinition"
                @select-attachment="selectFile"
              />
            </template>
            <template #tab-state="{ registerTab }">
              <InspectorSignalDetailCard
                v-if="mode === 'test' && autoSelectedSignalName != null"
                :ref="registerTab('state')"
                :signal="autoSelectedSignal"
              />
              <InspectorStateTab
                v-else
                :ref="registerTab('state')"
                :project-name="projectName"
                :selected-element="mode === 'test' ? autoSelectedElement : selectedGraphElement"
                :state-tokens="stateTabTokens"
                :fired-action-edge="firedActionEdge"
                :available-states="availableStates"
                :available-env-keys="availableEnvKeys"
                :selected-session="mode === 'test' ? autoSelectedSession : null"
                :session-input-tokens="mode === 'test' ? autoSessionInputTokens : null"
                :total-token-budget-per-session="totalTokenBudgetPerSession"
                :session-start-element="mode === 'test' ? autoSessionStartElement : null"
                :session-end-element="mode === 'test' ? autoSessionEndElement : null"
                :read-only="mode === 'test'"
                :editable-files="files"
                :highlighted-state-key="highlightedStateKey"
                :recently-added-key="recentlyAddedKey"
                :current-file-name="mode === 'edit' ? currentFileName : null"
                :deleting-file="deletingFile"
                :renaming-file="renamingFile"
                @select="handleTabSelect"
                @select-attachment="selectFile"
                @jump-to-attachment="handleJumpToAttachment"
                @set-field="handleSetSelectedElementField"
                @set-project-field="handleSetProjectField"
                @delete="handleDeleteSelectedElement"
                @open-actions-order="handleOpenActionsOrder"
                @add-state="handleAddState"
                @add-action="handleAddAction"
                @delete-file="handleDeleteFile"
                @rename-file="handleRenameFile"
              />
            </template>
            <template #tab-user="{ registerTab }">
              <InspectorUserInfoCard :ref="registerTab('user')" :user="autoSelectedUser" />
            </template>
            <template #tab-signals="{ registerTab }">
              <InspectorSignalsTab
                :ref="registerTab('signals')"
                :project-name="projectName"
                :signal-values="effectiveSignalValues"
                :editable-files="mode === 'edit' ? files : null"
                :state-key="mode === 'edit' ? selectedStateKey : highlightedStateKey"
                :recently-added-key="recentlyAddedKey"
                @jump-to-definition="(target) => jumpToDefinition(target, { silent: true })"
                @select-attachment="selectFile"
                @set-field="handleSetSignalField"
                @add-signal="handleAddSignal"
                @delete="handleDeleteSignal"
              />
            </template>
            <template #tab-metrics="{ registerTab }">
              <InspectorMetricsTab :ref="registerTab('metrics')" :until-message-id="untilMessageId" :project-name="projectName" />
            </template>
            <template #tab-env="{ registerTab }">
              <InspectorEnvTab :ref="registerTab('env')" :until-message-id="untilMessageId" :editable="envEditable" />
            </template>
            <template #tab-env-keys="{ registerTab }">
              <InspectorEnvKeysTab
                :ref="registerTab('env-keys')"
                :project-name="projectName"
                :recently-added-key="recentlyAddedKey"
                @jump-to-definition="(target) => jumpToDefinition(target, { silent: true })"
                @set-field="handleSetEnvKeyField"
                @add-env-key="handleAddEnvKey"
                @delete="handleDeleteEnvKey"
              />
            </template>
          </Inspector>
        </div>
      </div>
    </div>

    <div v-if="publishRemapPrompt" class="switch-dialog-overlay">
      <div class="switch-dialog">
        <p>
          The conversation's own current state ("{{ publishRemapPrompt.missing_state }}") no longer exists in this
          revision. Pick the state it now corresponds to before publishing.
        </p>
        <select v-model="publishRemapChoice" class="remap-select">
          <option disabled value="">Select a state…</option>
          <option v-for="key in publishRemapPrompt.available_states" :key="key" :value="key">{{ key }}</option>
        </select>
        <div class="switch-dialog-actions">
          <button
            class="switch-dialog-save-btn"
            :disabled="publishing || !publishRemapChoice"
            @click="confirmPublishRemap(publishRemapChoice)"
          >Publish</button>
          <button class="switch-dialog-cancel-btn" :disabled="publishing" @click="cancelPublishRemap">Cancel</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.edit-project-overlay {
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
  box-sizing: border-box;
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.edit-project-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.mode-segment {
  display: flex;
  gap: 0.2rem;
  padding: 0.2rem;
  border-radius: 8px;
  background: #eef1f5;
}

.mode-segment-btn {
  padding: 0.4rem 1rem;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
  font-size: 0.88rem;
  color: #555;
}

.mode-segment-btn:hover {
  color: #333;
}

.mode-segment-btn-active {
  background: white;
  color: #2c4d7a;
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12);
}

.publish-split-btn {
  position: relative;
  display: flex;
  align-items: stretch;
}

.publish-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  cursor: pointer;
}

/* The arrow half only ever renders alongside the main button (see
   canRevert) — rounding the main button's right edge only when it does
   keeps a plain, arrow-less "Rev. X" (nothing to revert to yet) looking
   like an ordinary single button rather than a split one missing a half. */
.publish-btn:has(+ .publish-menu-toggle) {
  border-right: none;
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
}

.publish-btn:hover:not(:disabled) {
  background: #256428;
}

.publish-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.publish-menu-toggle {
  padding: 0.4rem 0.5rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  border-top-left-radius: 0;
  border-bottom-left-radius: 0;
  background: #2e7d32;
  color: white;
  cursor: pointer;
  font-size: 0.7rem;
}

.publish-menu-toggle:hover {
  background: #256428;
}

.publish-menu-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 20;
  min-width: 11rem;
  padding: 0.3rem;
  border-radius: 8px;
  border: 1px solid #ddd;
  background: white;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
}

.publish-menu-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.45rem 0.6rem;
  border: none;
  border-radius: 5px;
  background: none;
  font-size: 0.82rem;
  color: #333;
  cursor: pointer;
}

.publish-menu-item:hover {
  background: #f0f4fa;
}

.publish-menu-item-danger {
  color: #c62828;
  font-weight: 700;
}

.publish-menu-item-danger:hover {
  background: #fdecea;
}

.remap-select {
  display: block;
  width: 100%;
  margin-bottom: 1rem;
  padding: 0.4rem 0.5rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  font-size: 0.85rem;
}

.edit-project-body {
  flex: 1;
  display: flex;
  min-height: 0;
  padding: 1rem;
}

/* Holds whichever one of ProjectDesignPanel/RunChat/
   ProjectTestPanel is actually showing (`mode` makes them mutually
   exclusive — see this file's own docstring) — each one is simply
   flex: 1 on its own now, no more "-full" override class needed to make
   it fill the column: with Design's own v-show hidden state
   contributing a display:none box (and Run/Test simply unmounted, see
   their own v-if), there's never a second sibling left to share space
   with in the first place. position: relative backs the leaving
   RunChat's absolute positioning below — its leave transition would
   otherwise still count as a flex:1 sibling for the ~0.18s it lingers
   in the DOM, squeezing whichever panel is entering. */
.edit-project-panels {
  position: relative;
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.panel-slide-bottom-enter-active,
.panel-slide-bottom-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
  position: absolute;
  inset: 0;
}

/* Leaving RunChat lingers ~0.18s as a positioned element (see above),
   which by itself would paint over a static-flow sibling that mounts in
   the same instant (e.g. ProjectTestPanel on switching to Test) —
   negative z-index drops it behind static siblings instead, so the
   panel actually being switched to is never hidden under a fading-out
   ghost of the old one. */
.panel-slide-bottom-leave-active {
  z-index: -1;
}

.panel-slide-bottom-enter-from,
.panel-slide-bottom-leave-to {
  opacity: 0;
  transform: translateY(16px);
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


/* Narrow screens: the inspector takes over the whole editor overlay, same
   as SignalsView's own narrow-screen behavior — there isn't room to dock
   it beside the editor and keep both usable. */
@media (max-width: 899.98px) {
  .inspector-divider {
    display: none;
  }
}

.inspector-wrap {
  display: flex;
  flex-direction: row;
  min-height: 0;
}

.inspector-panel {
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
  box-sizing: border-box;
  padding-top: var(--safe-area-top);
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  background: white;
  z-index: 150;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

@media (min-width: 900px) {
  .inspector-panel {
    /* Wide screens: docked beside the editor, both visible at once —
       width comes from the drag-adjusted --inspector-width variable. */
    position: static;
    inset: auto;
    padding: 0;
    z-index: auto;
    flex-shrink: 0;
    width: var(--inspector-width);
    border: 1px solid #ddd;
    border-radius: 8px;
    overflow: hidden;
    transition: width 0.15s ease;
  }
}

/* Collapsed (see Inspector.vue's own always-visible header toggle) —
   always just a slim docked strip, on any screen size, overriding the
   narrow-screen full-overlay behavior above (nothing to overlay: a
   collapsed panel has no tabs/body showing, see Inspector.vue's own
   v-show). */
.inspector-panel-collapsed {
  position: static !important;
  inset: auto !important;
  padding: 0 !important;
  z-index: auto !important;
  flex-shrink: 0;
  width: 2.4rem !important;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.switch-dialog-overlay {
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
  background: rgba(0, 0, 0, 0.35);
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
}

.switch-dialog {
  background: white;
  border-radius: 10px;
  padding: 1.2rem;
  max-width: 360px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
}

.switch-dialog p {
  margin: 0 0 1rem;
  font-size: 0.9rem;
  color: #333;
}

.switch-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

.switch-dialog-save-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  cursor: pointer;
  font-size: 0.85rem;
}

.switch-dialog-save-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.switch-dialog-cancel-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  background: white;
  color: #444;
  cursor: pointer;
  font-size: 0.85rem;
}

.switch-dialog-cancel-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
