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
import InspectorActionsTab from '../../inspector/InspectorActionsTab.vue'
import SessionDetailCard from '../../inspector/SessionDetailCard.vue'
import InspectorUserInfoCard from '../../inspector/InspectorUserInfoCard.vue'
import { useLeaveConfirmation } from '../../../composables/useLeaveConfirmation.js'
import {
  getProjectFiles,
  putProjectFile,
  putProjectFileBinary,
  deleteProjectFile,
  clearProjectHistory,
  getSignals,
  getSessionSignals,
  getSessions,
  getProjectGraph,
  getStateInputTokens,
  postAddState,
  postAddSignal,
  postAddEnvKey,
  postAddAction,
  putStateField,
  putActionField,
  putInitActionField,
  putProjectField,
  putSignalField,
  putEnvKeyField,
  putActionOrder,
  deleteState,
  deleteProjectAction,
  deleteProjectSignal,
  deleteProjectEnvKey,
  getProjectRevision,
  getPublishPreview,
  postPublishProject,
  postRevertProject,
  getUsers
} from '../../../api.js'
import { clearApiError, setApiError, setApiWarning } from '../../../errorStore.js'
import { confirmDialog, promptDialog, chooseDialog } from '../../../dialogStore.js'
import ErrorBanner from '../../ErrorBanner.vue'
import { refreshIdentifierRegistry } from '../../../identifierRegistry.js'
import { buildTimeline, highlightedStateKeyFor, nearestMessageIdAtOrBefore, resultingStateKeyFor, signalValuesFor } from '../../../benchmarkTimeline.js'
// `sessions` here is the *project's* whole session catalog (loaded by
// ProjectTestPanel.vue, the benchmark "Test" tab, via the live store) —
// unrelated to runSessions below, "Run" mode's own draft session pool,
// which just happens to share the same name in testStore.
import { sessions } from '../../../chatStore.js'
import { activeChatMode } from '../../../chatSkin.js'
import { setTestProject, testStore } from '../../../testChatStore.js'

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
  }
})

// One EditProjectView instance is always scoped to a single project for
// its whole lifetime — the "Run" tab's own test store targets it once here.
setTestProject(props.projectName)

const emit = defineEmits(['close', 'saved'])

// Upload (handleUploadFile below, and the file explorer's own hidden
// <input accept>) additionally allows every image extension the backend
// whitelists (see project_service.py's IMAGE_EXTENSIONS).
const IMAGE_PATTERN = /\.(png|jpe?g|gif|webp|svg)$/i
const UPLOADABLE_PATTERN = /\.(txt|ya?ml|css|png|jpe?g|gif|webp|svg)$/i
// Mirrors project_service.py's own MAX_IMAGE_UPLOAD_BYTES — checked here
// purely for immediate feedback; the backend enforces this authoritatively
// regardless.
const MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024

// "New aspect" seeds index.css with its three customizable regions,
// left empty — matches what both sample projects' own index.css style.
const INDEX_CSS_SKELETON = `.chat-header {
}

.chat-body {
}

.chat-footer {
}
`

function lineIndent(line) {
  const m = line.match(/^[ \t]*/)
  return m ? m[0].length : 0
}

function isBlankOrComment(trimmed) {
  return !trimmed || trimmed.startsWith('#')
}

// Best-effort line lookup for a top-level block's direct child key (e.g.
// `states:` -> a state name). A heuristic indentation scan, not a real
// YAML parse — relies on this app's own consistent 2-space indentation.
function findTopLevelChildLine(lines, topKey, childKey) {
  const topPattern = new RegExp(`^${topKey}\\s*:\\s*(#.*)?$`)
  let inBlock = false
  let childIndent = null
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]
    const trimmed = raw.trim()
    if (!inBlock) {
      if (lineIndent(raw) === 0 && topPattern.test(trimmed)) inBlock = true
      continue
    }
    if (isBlankOrComment(trimmed)) continue
    const indent = lineIndent(raw)
    if (indent === 0) break // left the block
    if (childIndent === null) childIndent = indent
    if (indent !== childIndent) continue // a nested field, not a direct child key
    const m = trimmed.match(/^(['"]?)([^:'"]+)\1\s*:\s*(#.*)?$/)
    if (m && m[2] === childKey) return i
  }
  return null
}

function findStateLine(lines, stateKey) {
  return findTopLevelChildLine(lines, 'states', stateKey)
}

function findSignalLine(lines, signalName) {
  return findTopLevelChildLine(lines, 'signals', signalName)
}

function findEnvKeyLine(lines, envKeyName) {
  return findTopLevelChildLine(lines, 'env', envKeyName)
}

// Finds the `attachments:` list item naming fileName within stateKey's
// block — a plain scalar list (`- filename`), unlike actions' own
// `- name: ...` mappings (see findActionLine).
function findAttachmentLine(lines, stateKey, fileName) {
  const stateLine = findStateLine(lines, stateKey)
  if (stateLine === null) return null
  const stateIndent = lineIndent(lines[stateLine])
  let inAttachments = false
  let attachmentsIndent = null
  for (let i = stateLine + 1; i < lines.length; i++) {
    const raw = lines[i]
    const trimmed = raw.trim()
    if (isBlankOrComment(trimmed)) continue
    const indent = lineIndent(raw)
    if (indent <= stateIndent) break // left the state's own block
    if (!inAttachments) {
      if (/^attachments\s*:\s*(#.*)?$/.test(trimmed)) {
        inAttachments = true
        attachmentsIndent = indent
      }
      continue
    }
    if (indent <= attachmentsIndent) break // left the attachments: list
    const m = trimmed.match(/^-\s*(['"]?)(.*)\1\s*$/)
    if (m && m[2] === fileName) return i
  }
  return null
}

// The init-action has no source state to search under (see
// InspectorGraphTab.vue's isInitEdge) — a bare top-level key, not a
// states: child, so findActionLine's state-block scan doesn't apply.
function findInitActionLine(lines) {
  const idx = lines.findIndex((line) => lineIndent(line) === 0 && /^init-action\s*:\s*(#.*)?$/.test(line.trim()))
  return idx === -1 ? null : idx
}

// Within stateKey's block, finds the line starting the action list item
// (the `- name: ...` line, wherever `name:` actually falls inside it)
// whose name matches actionName.
function findActionLine(lines, stateKey, actionName) {
  const stateLine = findStateLine(lines, stateKey)
  if (stateLine === null) return null
  const stateIndent = lineIndent(lines[stateLine])
  let inActions = false
  let itemStart = null
  let itemMatches = false

  const flushItem = () => (itemStart !== null && itemMatches ? itemStart : null)

  for (let i = stateLine + 1; i <= lines.length; i++) {
    const atEnd = i === lines.length
    const raw = atEnd ? '' : lines[i]
    const trimmed = raw.trim()
    const skippable = isBlankOrComment(trimmed)
    const indent = skippable ? null : lineIndent(raw)

    const leavingState = atEnd || (!skippable && indent <= stateIndent)
    const startsNewItem = !skippable && !leavingState && trimmed.startsWith('- ')

    if (leavingState || startsNewItem) {
      const found = flushItem()
      if (found !== null) return found
      if (leavingState) return null
      itemStart = i
      itemMatches = false
    }

    if (!skippable && !leavingState) {
      if (!inActions) {
        if (/^actions\s*:\s*(#.*)?$/.test(trimmed)) inActions = true
      } else if (itemStart !== null) {
        const m = trimmed.match(/^-?\s*name\s*:\s*(['"]?)(.*?)\1\s*(#.*)?$/)
        if (m && m[2] === actionName) itemMatches = true
      }
    }
  }
  return null
}

const filesLoading = ref(true)
const files = ref([])
const currentFileName = ref('index.yml')

const uploading = ref(false)
const creatingFile = ref(false)
const deletingFile = ref(null)

// designPanelRef is this view's handle onto ProjectDesignPanel;
// codeEditorRef/indexYmlEditorRef/indexCssEditorRef are computed proxies
// through to the refs it exposes via defineExpose (which auto-unwraps).
const designPanelRef = ref(null)
// Whichever one is actually mounted (see ProjectDesignPanel.vue's
// v-if/v-else, keyed off currentFileName === 'index.yml') — each owns
// its own loading/saving/isDirty state internally (see activeEditorIsDirty below).
const codeEditorRef = computed(() => designPanelRef.value?.codeEditorRef ?? null)
const indexYmlEditorRef = computed(() => designPanelRef.value?.indexYmlEditorRef ?? null)
const indexCssEditorRef = computed(() => designPanelRef.value?.indexCssEditorRef ?? null)
const mdEditorRef = computed(() => designPanelRef.value?.mdEditorRef ?? null)
// An image has no editor at all (see the file explorer's own <img>
// preview branch below) — never dirty, nothing for activeEditor() to
// save/discard.
const currentFileIsImage = computed(() => IMAGE_PATTERN.test(currentFileName.value ?? ''))
// A .txt/.md attachment gets MdEditorPanel's Preview/Edit toggle instead
// of the bare CodeEditor fallback (see ProjectDesignPanel.vue).
const currentFileIsMarkdown = computed(() => /\.(md|txt)$/i.test(currentFileName.value ?? ''))
// Whether the file explorer's "Behavior" node itself (index.yml) is the
// open file — as opposed to one of its attachments or anything under
// "Theme". Only then does the Inspector have states/actions/signals to
// show at all (see inspectorTabs and InspectorStateTab's own gating).
const isBehaviorNodeSelected = computed(() => currentFileName.value === 'index.yml')
// The file explorer's "Theme" branch children — every image asset index.css's
// own url(...) rules could reference. Deleting index.css takes these down
// with it (see handleDeleteFile) since an asset with no stylesheet left to
// reference it is just dead weight.
const themeAssetNames = computed(() => files.value.filter((name) => name !== 'index.css' && IMAGE_PATTERN.test(name)))
// RunChat.vue's "Apply aspect" toggle only makes sense once a theme
// actually exists to apply.
const hasTheme = computed(() => files.value.includes('index.css'))
const activeEditorIsDirty = computed(() => {
  if (currentFileName.value === 'index.yml') return indexYmlEditorRef.value?.isDirty ?? false
  if (currentFileName.value === 'index.css') return indexCssEditorRef.value?.isDirty ?? false
  if (currentFileIsImage.value) return false
  if (currentFileIsMarkdown.value) return mdEditorRef.value?.isDirty ?? false
  return codeEditorRef.value?.isDirty ?? false
})

// Inspect panel: the shared Inspector component shows the last-saved
// project's state graph, signal definitions, and metrics_framework's core
// metrics. `inspectorRef` drives reload-after-save, Metrics refresh, and graph resize on drag.
const inspecting = ref(true)
const inspectorRef = ref(null)
const inspectorWidth = ref(360)
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

// Resolved off index.yml's own already-loaded graph data (see
// IndexYmlEditorPanel.vue's stateElementFor/actionsForState) rather than
// a second fetch — null/[] whenever nothing is selected.
const stateTabElement = computed(() => {
  const key = selectedStateKey.value
  return key == null ? null : (indexYmlEditorRef.value?.stateElementFor(key) ?? null)
})
// No selection at all means an empty list — showing the init-action's
// own state (key "") here without anything actually selected in the
// Graph would make "Actions" look like a selection exists when it doesn't.
const actionsTabList = computed(() => {
  const key = selectedStateKey.value
  return key == null ? [] : (indexYmlEditorRef.value?.actionsForState(key) ?? [])
})

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

// A selected session's own start/end state — same resolution as
// LabelProjectView.vue's own Info tab (see its sessionStartStateKey/
// sessionEndStateKey docstring): an imported session never actually ran
// against the automaton, so its first/last expert-annotated expected_state
// stands in for start_state/end_state.
const autoSessionSignals = ref([])
watch(autoSelectedSessionId, async (id) => {
  autoSessionSignals.value = id == null ? [] : await getSessionSignals(id).catch(() => [])
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
// shows the live conversation's Metrics/Env; edit mode shows index.yml's own Info/Actions/Signals/Env-keys instead.
// Test mode only ever shows Info — plain read-only viewing, no Actions/
// Signals/Env-keys editing surface makes sense while browsing test results.
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
    { id: 'actions', label: 'Actions' },
    { id: 'signals', label: 'Signals' },
    { id: 'env-keys', label: 'Env' }
  ]
})
const inspectorActiveTab = ref('states')

// A selection made in edit mode drives the Inspector's "State"/"Actions"
// tab too — the user shouldn't have to manually click over to it.
watch(selectedGraphElement, (element) => {
  if (mode.value !== 'edit' || !element) return
  inspectorActiveTab.value = element.kind === 'state' ? 'state' : 'actions'
})
// Live value/error per signal name — fed to the Inspector's signal-values
// prop, refreshed on its own cadence (see refreshSignalValues).
const signalValueByName = ref({})

// The live session's Signals event log and starting state — feeds the
// same clickable message+transition timeline as LabelProjectView.vue's
// review one (see ChatTimeline.vue/benchmarkTimeline.js), just kept live.
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
    id: m.messageId ?? null,
    timestamp: m.timestamp,
    role: m.role,
    content: m.content,
    audio_text: m.audioText,
    reaction: m.reaction
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
  // Common point every project edit funnels through — refreshing the
  // shared trigger-autocomplete registry here (see identifierRegistry.js)
  // covers every way a signal/action can come into existence.
  refreshIdentifierRegistry(props.projectName)
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
async function restartAndPrefill(message) {
  await handleTruncateFrom(message.timestamp)
  selected.value = null
  draft.value = message.content
}

async function restartAndResend(message) {
  await handleTruncateFrom(message.timestamp)
  selected.value = null
  await handleSend(message.content)
}

// A definition clicked in the Inspect panel to jump the editor's cursor
// to (see jumpToDefinition/applyPendingCursorTarget). Cleared once applied.
const pendingCursorTarget = ref(null)

// Single "Design | Run | Test" segmented control: 'edit' shows the file
// explorer + editor, 'run' shows the embedded chat, 'test' shows the
// replay tree. Also gates the Inspector's tab set (see inspectorTabs).
const mode = ref('edit')
const editorOpen = computed(() => mode.value === 'edit')
const runOpen = computed(() => mode.value === 'run')
const testOpen = computed(() => mode.value === 'test')

// Whichever state key the "State" Inspector tab is actually showing right
// now — stateTabElement in edit mode, autoSelectedElement's own key while
// browsing a run (see InspectorStateTab's :selected-element binding below).
const stateTabTokensKey = computed(() => (mode.value === 'test' ? autoSelectedStateKey.value : selectedStateKey.value))

// Estimated input-token cost of that state's own turn prompt (see backend
// ProjectInspector.get_state_input_tokens) — null while unknown/loading,
// or when nothing is selected. Fetched on demand per state rather than
// bundled into the Graph fetch, since it can cost a real provider call.
const stateTabTokens = ref(null)
let stateTabTokensRequestId = 0

async function refreshStateTabTokens() {
  const key = stateTabTokensKey.value
  if (key == null) {
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
  if (next === 'test') ensureUsersList()
}

onBeforeUnmount(() => { activeChatMode.value = 'live' })

// Left panel width in px, adjusted by dragging the split divider.
const explorerWidth = ref(220)
// Which divider (if any) is currently being dragged — 'explorer' or
// 'inspector' — read by the single shared onDrag/stopDrag pair below.
let dragTarget = null

function activeEditor() {
  if (currentFileName.value === 'index.yml') return indexYmlEditorRef.value
  if (currentFileName.value === 'index.css') return indexCssEditorRef.value
  if (currentFileIsImage.value) return null
  if (currentFileIsMarkdown.value) return mdEditorRef.value
  return codeEditorRef.value
}

async function loadFiles() {
  filesLoading.value = true
  try {
    files.value = (await getProjectFiles(props.projectName)).files
  } catch {
    // already surfaced via apiFetch
  } finally {
    filesLoading.value = false
  }
}

// Moves the editor's cursor to a definition clicked in the Inspect panel
// (see jumpToDefinition). Best-effort: a target that findStateLine/
// findActionLine/findSignalLine can't locate just leaves the cursor as-is.
function applyPendingCursorTarget() {
  if (!pendingCursorTarget.value) return
  const text = indexYmlEditorRef.value?.content
  if (!text) return
  const target = pendingCursorTarget.value
  pendingCursorTarget.value = null
  const lines = text.split('\n')
  let lineIndex = null
  if (target.kind === 'state') lineIndex = findStateLine(lines, target.stateKey)
  else if (target.kind === 'action') {
    lineIndex = target.stateKey === '' ? findInitActionLine(lines) : findActionLine(lines, target.stateKey, target.actionName)
  } else if (target.kind === 'signal') lineIndex = findSignalLine(lines, target.signalName)
  else if (target.kind === 'env-key') lineIndex = findEnvKeyLine(lines, target.envKeyName)
  else if (target.kind === 'attachment') lineIndex = findAttachmentLine(lines, target.stateKey, target.fileName)
  if (lineIndex === null) return
  indexYmlEditorRef.value?.jumpToLine(lineIndex)
}

// Switches to index.yml first if it isn't already open (the only file
// definitions live in). `silent` suppresses that switch, so a plain row
// selection doesn't yank the user out of whatever file they're viewing.
async function jumpToDefinition(target, { silent = false } = {}) {
  pendingCursorTarget.value = target
  if (currentFileName.value !== 'index.yml') {
    if (silent) {
      pendingCursorTarget.value = null
      return
    }
    await selectFile('index.yml')
    if (currentFileName.value !== 'index.yml') return // blocked by the unsaved-changes dialog
  }
  await nextTick()
  while (indexYmlEditorRef.value && !indexYmlEditorRef.value.content) {
    await new Promise((resolve) => setTimeout(resolve, 20))
  }
  applyPendingCursorTarget()
}

function switchFile(fileName) {
  currentFileName.value = fileName
  // selectedGraphElement is deliberately left alone here — the Inspector's
  // "State"/"Actions" selection stays valid while browsing another file.
}

// Every entry point that would discard unsaved code routes through here
// instead of running `run` directly: dirty means ask first (via
// runGuardedAction's chooseDialog), clean runs immediately.
function guardedAction(label, run) {
  if (!activeEditorIsDirty.value) {
    run()
    return
  }
  runGuardedAction(label, run)
}

async function runGuardedAction(label, run) {
  const choice = await chooseDialog({
    title: 'Unsaved changes',
    body: `"${currentFileName.value}" has unsaved changes. Save before you ${label}?`,
    options: [
      { id: 'save', label: 'Save' },
      { id: 'discard', label: 'Discard' }
    ]
  })
  if (choice === 'save') {
    if (await activeEditor()?.save?.()) run()
    return
  }
  if (choice === 'discard') {
    // The whole point of "Discard": the active editor's dirty buffer
    // actually reverts to its last-loaded content.
    activeEditor()?.discard?.()
    run()
    return
  }
  // null (Cancel/backdrop/ESC) — a cursor jump that triggered this action
  // is moot once it's declined, so it shouldn't fire on some later,
  // unrelated action either.
  pendingCursorTarget.value = null
}

// Entry point for both explorer clicks and post-upload auto-open.
function selectFile(fileName) {
  if (fileName === currentFileName.value) return
  guardedAction(`switch to "${fileName}"`, () => switchFile(fileName))
}

// Common tail for every Add/edit/delete/reorder handler below: the
// backend already persisted the change, so both index.yml's dedicated
// view and the Inspector need to catch up (same as handleFileSaved).
async function refreshAfterProjectEdit() {
  await indexYmlEditorRef.value?.refresh(false)
  await indexYmlEditorRef.value?.reloadCode()
  if (inspecting.value) await inspectorRef.value?.refresh()
  refreshValidStateKeys()
  refreshProjectRevision()
  // Every edit funnels through here, so this is the single choke point
  // that keeps the selected state's "Tokens: X" estimate from going
  // stale after a save (its cache key is content-addressed backend-side,
  // so a changed prompt naturally recomputes rather than serving a stale hit).
  refreshStateTabTokens()
}

// {revision, published_revision} — null while not yet loaded. A save can
// fork (see Db.save_project_files' fork-on-first-edit-after-publish),
// bumping `revision` — refreshed after every save and every publish.
const projectRevision = ref(null)
const publishing = ref(false)
// Set only while ProjectService.preview_publish reported needs_remap —
// the modal below is shown exactly while this is non-null. Cleared on
// both confirm and cancel.
const publishRemapPrompt = ref(null)
const publishRemapChoice = ref('')
// Set only while handleClose's "publish before leaving?" confirm was
// accepted — handlePublish's success paths close the view once the
// publish lands; every other exit clears this instead, so a later, unrelated Publish click never closes the view too.
const closeAfterPublish = ref(false)

async function refreshProjectRevision() {
  try {
    projectRevision.value = await getProjectRevision(props.projectName)
  } catch {
    // already surfaced via apiFetch
  }
}

const publishUpToDate = computed(
  () => projectRevision.value != null && projectRevision.value.revision === projectRevision.value.published_revision
)

// A real publish/revert invalidates every user's undo/redo history
// server-side; refreshAfterProjectEdit already re-pulls index.yml's
// buffer, so this only matters when a *different* file is open.
async function refreshActiveEditorHistory() {
  if (currentFileName.value === 'index.yml') return
  await activeEditor()?.reload?.()
}

// Closes the view once a publish handleClose asked for actually lands
// (see closeAfterPublish) — called by both handlePublish's direct-success
// path and confirmPublishRemap's, once resolved.
function closeIfRequested() {
  if (!closeAfterPublish.value) return
  closeAfterPublish.value = false
  emit('close')
}

function resetCloseAfterPublish() {
  closeAfterPublish.value = false
}

async function handlePublish() {
  if (publishUpToDate.value || publishing.value) {
    resetCloseAfterPublish()
    return
  }
  publishing.value = true
  try {
    const preview = await getPublishPreview(props.projectName)
    if (preview.needs_remap) {
      publishRemapChoice.value = ''
      publishRemapPrompt.value = preview
      return
    }
    // Only ask when it's actually consequential — a live conversation
    // still running on the currently published revision.
    if (preview.has_active_sessions) {
      const ok = await confirmDialog({
        title: 'Publish',
        body: `Publish revision ${projectRevision.value?.revision}? There's an active session on the currently published revision — it will stay frozen there; this one becomes the new one.`,
        okLabel: 'Publish',
        danger: true
      })
      if (!ok) {
        resetCloseAfterPublish()
        return
      }
    }
    projectRevision.value = await postPublishProject(props.projectName)
    await refreshActiveEditorHistory()
    closeIfRequested()
  } catch {
    // already surfaced via apiFetch
    resetCloseAfterPublish()
  } finally {
    publishing.value = false
  }
}

async function confirmPublishRemap(stateKey) {
  publishing.value = true
  try {
    projectRevision.value = await postPublishProject(props.projectName, stateKey)
    publishRemapPrompt.value = null
    await refreshActiveEditorHistory()
    closeIfRequested()
  } catch {
    // already surfaced via apiFetch — leave the modal open so the user
    // can pick a different state or cancel
  } finally {
    publishing.value = false
  }
}

function cancelPublishRemap() {
  publishRemapPrompt.value = null
  publishing.value = false
  resetCloseAfterPublish()
}

// The "Rev. X" split button's dropdown arrow — only rendered when
// there's both a draft ahead of the published revision and a prior
// publication to revert to.
const canRevert = computed(
  () => !publishUpToDate.value && projectRevision.value?.published_revision != null
)
const publishMenuOpen = ref(false)
function closePublishMenu() {
  publishMenuOpen.value = false
}
function handleDocumentClickForPublishMenu(event) {
  if (publishMenuOpen.value && !event.target.closest('.publish-split-btn')) closePublishMenu()
}
onMounted(() => document.addEventListener('click', handleDocumentClickForPublishMenu))
onBeforeUnmount(() => document.removeEventListener('click', handleDocumentClickForPublishMenu))

async function handleRevert() {
  if (!canRevert.value || publishing.value) return
  const targetRevision = projectRevision.value.published_revision
  const ok = await confirmDialog({
    title: 'Revert',
    body: `Revert to rev. ${targetRevision}? This permanently discards every unpublished change on rev. ${projectRevision.value.revision} — there's no undo for this.`,
    okLabel: 'Revert',
    danger: true
  })
  if (!ok) return
  publishing.value = true
  try {
    await postRevertProject(props.projectName)
    selectedGraphElement.value = null
    await refreshAfterProjectEdit()
    await refreshActiveEditorHistory()
  } catch {
    // already surfaced via apiFetch
  } finally {
    publishing.value = false
  }
}

function handleAddState() {
  guardedAction('add a new state', async () => {
    try {
      const state = await postAddState(props.projectName)
      await refreshAfterProjectEdit()
      selectedGraphElement.value = indexYmlEditorRef.value?.stateElementFor(state.key) ?? null
      flashRecentlyAdded(`state:${state.key}`)
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleAddSignal() {
  guardedAction('add a new signal', async () => {
    try {
      const signal = await postAddSignal(props.projectName)
      await refreshAfterProjectEdit()
      flashRecentlyAdded(`signal:${signal.name}`)
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleAddEnvKey() {
  guardedAction('add a new env key', async () => {
    try {
      const envKey = await postAddEnvKey(props.projectName)
      await refreshAfterProjectEdit()
      flashRecentlyAdded(`env-key:${envKey.name}`)
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleAddAction() {
  const stateKey = selectedStateKey.value
  if (!stateKey) return
  guardedAction('add a new action', async () => {
    try {
      const action = await postAddAction(props.projectName, stateKey)
      await refreshAfterProjectEdit()
      // Selects the new action itself, not its containing state — selecting
      // the state would flip the Inspector's active tab back to "State" (see the selectedGraphElement watch above).
      selectedGraphElement.value = indexYmlEditorRef.value?.actionsForState(stateKey).find(
        (a) => a.data.actionName === action.name
      ) ?? null
      flashRecentlyAdded(`action:${stateKey}/${action.name}`)
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleSetStateField(stateName, field, value) {
  guardedAction(`edit "${field}"`, async () => {
    try {
      await putStateField(props.projectName, stateName, field, value)
      await refreshAfterProjectEdit()
      selectedGraphElement.value = indexYmlEditorRef.value?.stateElementFor(stateName) ?? null
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleSetProjectField(field, value) {
  guardedAction(`edit "${field}"`, async () => {
    try {
      await putProjectField(props.projectName, field, value)
      await refreshAfterProjectEdit()
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleSetActionField(stateName, actionName, field, value) {
  guardedAction(`edit "${field}"`, async () => {
    try {
      // The init-action (stateName '') lives outside `states:` entirely,
      // so putActionField's state/action lookup can't reach it — its
      // fields go through the dedicated endpoint instead.
      if (stateName === '') {
        await putInitActionField(props.projectName, field, value)
      } else {
        await putActionField(props.projectName, stateName, actionName, field, value)
      }
      await refreshAfterProjectEdit()
      selectedGraphElement.value = indexYmlEditorRef.value?.actionsForState(stateName).find(
        (a) => a.data.actionName === actionName
      ) ?? null
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleSetSignalField(signalName, field, value) {
  guardedAction(`edit "${field}"`, async () => {
    try {
      const signal = await putSignalField(props.projectName, signalName, field, value)
      await refreshAfterProjectEdit()
      // Only a ui-label edit can rename the signal — its line in the YAML
      // moves, so re-jump to it off the *new* name the response reported.
      if (field === 'ui-label') await jumpToDefinition({ kind: 'signal', signalName: signal.name }, { silent: true })
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleSetEnvKeyField(envKeyName, field, value) {
  guardedAction(`edit "${field}"`, async () => {
    try {
      const envKey = await putEnvKeyField(props.projectName, envKeyName, field, value)
      await refreshAfterProjectEdit()
      // Only a 'name' edit can rename the key — its line in the YAML
      // moves, so re-jump to it off the *new* name the response reported.
      if (field === 'name') await jumpToDefinition({ kind: 'env-key', envKeyName: envKey.name }, { silent: true })
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleDeleteState(stateName) {
  guardedAction('delete this state', async () => {
    try {
      await deleteState(props.projectName, stateName)
      selectedGraphElement.value = null
      await refreshAfterProjectEdit()
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleDeleteAction(stateName, actionName) {
  guardedAction('delete this action', async () => {
    try {
      await deleteProjectAction(props.projectName, stateName, actionName)
      // The containing state is still selected — only the action itself
      // (if it happened to be the literal selection) is now gone.
      if (selectedGraphElement.value?.kind === 'action' && selectedGraphElement.value.data.actionName === actionName) {
        selectedGraphElement.value = indexYmlEditorRef.value?.stateElementFor(stateName) ?? null
      }
      await refreshAfterProjectEdit()
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleDeleteSignal(signalName) {
  guardedAction('delete this signal', async () => {
    try {
      await deleteProjectSignal(props.projectName, signalName)
      await refreshAfterProjectEdit()
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleDeleteEnvKey(envKeyName) {
  guardedAction('delete this env key', async () => {
    try {
      await deleteProjectEnvKey(props.projectName, envKeyName)
      await refreshAfterProjectEdit()
    } catch {
      // already surfaced via apiFetch
    }
  })
}

function handleReorderAction({ actionName, position }) {
  const stateKey = selectedStateKey.value
  if (!stateKey) return
  guardedAction('reorder actions', async () => {
    try {
      await putActionOrder(props.projectName, stateKey, actionName, position)
      await refreshAfterProjectEdit()
    } catch {
      // already surfaced via apiFetch
    }
  })
}

// Common tail for a successful Save on either kind of editor — re-emitted
// up plus the same refreshes refreshAfterProjectEdit triggers, minus
// reloading the buffer that was just the one doing the saving.
async function handleFileSaved() {
  emit('saved')
  // A manual Save from the Code segment writes index.yml directly too, so
  // the Graph segment's cytoscape data needs the same refresh. No-op via
  // optional chaining when a *different* file was the one just saved.
  await indexYmlEditorRef.value?.refresh(false)
  if (inspecting.value) await inspectorRef.value?.refresh()
  refreshValidStateKeys()
  refreshProjectRevision()
}

// The Inspector's "State"/"Actions" tabs share the same selection the
// Graph drives, but a row click here only emits 'select' — this is the
// tab-side equivalent of a Graph click's select + jump-to-definition. Silent: shouldn't yank the user into the Code segment.
function handleTabSelect(element) {
  selectedGraphElement.value = element
  if (!element) return
  if (element.kind === 'state') jumpToDefinition({ kind: 'state', stateKey: element.data.id }, { silent: true })
  else jumpToDefinition(
    { kind: 'action', stateKey: element.data.matchStateKey, actionName: element.data.actionName },
    { silent: true }
  )
}

// The state edit form's attachment buttons (see InspectorDetailCard.vue's
// selectAttachment) jump to where the file is declared in index.yml
// rather than opening it, unlike every other attachment button elsewhere.
function handleJumpToAttachment(fileName) {
  const stateKey = stateTabElement.value?.data.id
  if (stateKey == null) return
  jumpToDefinition({ kind: 'attachment', stateKey, fileName }, { silent: true })
}

async function handleUploadFile(event) {
  const files = Array.from(event.target.files ?? [])
  event.target.value = '' // reset so re-selecting the same file(s) re-fires change
  if (!files.length) return

  const invalidNames = files.filter((file) => !UPLOADABLE_PATTERN.test(file.name)).map((file) => file.name)
  if (invalidNames.length) {
    setApiError(
      `Only .txt, .yml/.yaml, .css, or image (.png/.jpg/.gif/.webp/.svg) files can be uploaded — ` +
      `${invalidNames.map((name) => `"${name}"`).join(', ')} ${invalidNames.length === 1 ? "isn't" : "aren't"}.`
    )
    return
  }
  const oversizedNames = files
    .filter((file) => IMAGE_PATTERN.test(file.name) && file.size > MAX_IMAGE_UPLOAD_BYTES)
    .map((file) => file.name)
  if (oversizedNames.length) {
    setApiError(
      `${oversizedNames.map((name) => `"${name}"`).join(', ')} ` +
      `${oversizedNames.length === 1 ? 'is' : 'are'} larger than the 5 MB upload limit.`
    )
    return
  }

  uploading.value = true
  clearApiError()
  try {
    for (const file of files) {
      if (IMAGE_PATTERN.test(file.name)) {
        await putProjectFileBinary(props.projectName, file.name, file)
      } else {
        const text = await file.text()
        await putProjectFile(props.projectName, file.name, text)
      }
    }
    await loadFiles()
    await selectFile(files[files.length - 1].name)
  } catch {
    // already surfaced via apiFetch
  } finally {
    uploading.value = false
  }
}

function toMdFileName(base) {
  return `${base.replace(/\.md$/i, '')}.md`
}

async function createProjectFile(name, content) {
  creatingFile.value = true
  clearApiError()
  try {
    await putProjectFile(props.projectName, name, content)
    await loadFiles()
    await selectFile(name)
  } catch {
    // already surfaced via apiFetch
  } finally {
    creatingFile.value = false
  }
}

// validate runs inline as the user types, so the existence error shows
// right under the field instead of bouncing off setApiError after the
// prompt's already closed.
async function handleNewAttachment() {
  const rawName = await promptDialog({
    title: 'New attachment',
    body: 'Attachment name (always saved as .md):',
    placeholder: 'notes',
    validate(value) {
      const trimmed = value.trim()
      if (!trimmed) return 'Enter a file name.'
      if (files.value.includes(toMdFileName(trimmed))) return `A file named "${toMdFileName(trimmed)}" already exists.`
      return null
    }
  })
  if (rawName === null) return // cancelled
  await createProjectFile(toMdFileName(rawName.trim()), '')
}

async function handleNewAspect() {
  if (files.value.includes('index.css')) return
  await createProjectFile('index.css', INDEX_CSS_SKELETON)
}

// index.yml is protected server-side too (delete_project_file rejects it) —
// the button is also hidden for it in the template, this is just a second
// guard against a stale click.
async function handleDeleteFile(fileName) {
  if (fileName === 'index.yml') return
  // The asset list here is only for the confirm prompt's own wording —
  // the cascade itself (deleting every asset along with index.css) is
  // server-side, see ProjectService.delete_project_file.
  const cascadeAssets = fileName === 'index.css' ? themeAssetNames.value : []
  // A lone Theme asset is a single, cheap, easily re-uploaded file with
  // nothing cascading from it — index.css (which does cascade) and every
  // other file still confirm.
  if (!IMAGE_PATTERN.test(fileName)) {
    const confirmMessage = cascadeAssets.length
      ? `Delete "index.css"? This also deletes the ${cascadeAssets.length} asset${cascadeAssets.length === 1 ? '' : 's'} it can reference: ${cascadeAssets.join(', ')}.\n\nThis cannot be undone.`
      : `Delete file "${fileName}"? This cannot be undone.`
    const ok = await confirmDialog({ title: 'Delete file', body: confirmMessage, okLabel: 'Delete', danger: true })
    if (!ok) return
  }
  deletingFile.value = fileName
  clearApiError()
  try {
    await deleteProjectFile(props.projectName, fileName)
    await loadFiles()
    if (fileName === currentFileName.value || cascadeAssets.includes(currentFileName.value)) {
      await switchFile('index.yml')
    }
  } catch {
    // already surfaced via apiFetch
  } finally {
    deletingFile.value = null
  }
}

// Only prompts when there's actually something to lose — a clean editor
// (nothing typed, or already saved) closes straight away. Undo/redo
// history itself is cleared on entry, not here — see onMounted.
const { confirmLeaveIfNeeded } = useLeaveConfirmation(activeEditorIsDirty, 'Discard unsaved changes to this file?')

// Unsaved-file changes are checked first — the more urgent, data-loss
// concern. Only past that, and only when there's actually a pending
// revision to decide about, does a three-way choice show: publish before
// leaving, leave it pending, or cancel the close outright.
async function handleClose() {
  if (!(await confirmLeaveIfNeeded())) return
  if (publishUpToDate.value) {
    emit('close')
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
    closeAfterPublish.value = true
    handlePublish()
    return
  }
  if (choice === 'leave') emit('close')
  // null (Cancel/backdrop/ESC) — stay open, nothing to do.
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

function startExplorerDrag(event) {
  dragTarget = 'explorer'
  event.preventDefault()
}

function startInspectorDrag(event) {
  dragTarget = 'inspector'
  event.preventDefault()
}

function onDrag(event) {
  if (dragTarget === 'explorer') {
    explorerWidth.value = Math.min(420, Math.max(160, explorerWidth.value + event.movementX))
  } else if (dragTarget === 'inspector') {
    // The inspector's divider sits on its left edge, so dragging it left
    // (negative movementX) needs to grow the panel, not shrink it.
    inspectorWidth.value = Math.min(560, Math.max(240, inspectorWidth.value - event.movementX))
    inspectorRef.value?.resize()
  }
}

function stopDrag() {
  dragTarget = null
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
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', stopDrag)
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
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', stopDrag)
  window.removeEventListener('resize', handleWindowResize)
})
</script>

<template>
  <div class="edit-project-overlay">
    <div class="edit-project-header">
      <h2>Edit project — {{ projectName }}</h2>
      <div class="edit-project-header-actions">
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
        <button class="close-btn" @click="handleClose">Back</button>
      </div>
    </div>

    <ErrorBanner />

    <div class="edit-project-body">
      <div class="edit-project-panels">
        <ProjectDesignPanel
          v-show="editorOpen"
          ref="designPanelRef"
          :project-name="projectName"
          :files="files"
          :files-loading="filesLoading"
          :current-file-name="currentFileName"
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
          @select-file="selectFile"
          @upload-file="handleUploadFile"
          @jump-to-definition="(target) => jumpToDefinition(target, { silent: true })"
          @select="selectedGraphElement = $event"
          @saved="handleFileSaved"
        />

        <Transition name="panel-slide-bottom">
          <RunChat
            v-if="runOpen"
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
              <InspectorStateTab
                :ref="registerTab('state')"
                :project-name="projectName"
                :selected-element="mode === 'test' ? autoSelectedElement : stateTabElement"
                :state-tokens="stateTabTokens"
                :selected-session="mode === 'test' ? autoSelectedSession : null"
                :session-start-element="mode === 'test' ? autoSessionStartElement : null"
                :session-end-element="mode === 'test' ? autoSessionEndElement : null"
                :read-only="mode === 'test'"
                :editable-files="files"
                :highlighted-state-key="highlightedStateKey"
                :recently-added-key="recentlyAddedKey"
                :current-file-name="mode === 'edit' ? currentFileName : null"
                :deleting-file="deletingFile"
                @select="handleTabSelect"
                @select-attachment="selectFile"
                @jump-to-attachment="handleJumpToAttachment"
                @set-field="(field, value) => handleSetStateField(stateTabElement?.data.id, field, value)"
                @set-project-field="handleSetProjectField"
                @delete="handleDeleteState"
                @add-state="handleAddState"
                @delete-file="handleDeleteFile"
              />
            </template>
            <template #tab-user="{ registerTab }">
              <InspectorUserInfoCard :ref="registerTab('user')" :user="autoSelectedUser" />
            </template>
            <template #tab-actions="{ registerTab }">
              <InspectorActionsTab
                :ref="registerTab('actions')"
                :actions="actionsTabList"
                :editable-files="files"
                :selected-element="selectedGraphElement"
                :fired-action-edge="firedActionEdge"
                :highlighted-state-key="highlightedStateKey"
                :available-states="availableStates"
                :allow-add="!!selectedStateKey"
                :recently-added-key="recentlyAddedKey"
                @select="handleTabSelect"
                @select-attachment="selectFile"
                @reorder="handleReorderAction"
                @set-field="handleSetActionField"
                @delete="handleDeleteAction"
                @add-action="handleAddAction"
              />
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
  inset: 0;
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.edit-project-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.edit-project-header h2 {
  margin: 0;
  font-size: 1.1rem;
}


.edit-project-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.close-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.close-btn:hover {
  background: #4a6fa5;
  color: white;
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
  inset: 0;
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
  z-index: auto !important;
  flex-shrink: 0;
  width: 2.4rem !important;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.switch-dialog-overlay {
  position: fixed;
  inset: 0;
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
