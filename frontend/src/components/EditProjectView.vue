<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ChatWindow from './chat/ChatWindow.vue'
import ChatTimeline from './chat/ChatTimeline.vue'
import RestartFromHereButton from './chat/RestartFromHereButton.vue'
import ModelMenu from './ModelMenu.vue'
import Inspector from './inspector/Inspector.vue'
import InspectorGraphTab from './inspector/InspectorGraphTab.vue'
import InspectorSignalsTab from './inspector/InspectorSignalsTab.vue'
import InspectorMetricsTab from './inspector/InspectorMetricsTab.vue'
import InspectorEnvTab from './inspector/InspectorEnvTab.vue'
import InspectorStateTab from './inspector/InspectorStateTab.vue'
import InspectorActionsTab from './inspector/InspectorActionsTab.vue'
import CodeEditor from './CodeEditor.vue'
import IndexYmlEditorView from './IndexYmlEditorView.vue'
import {
  getProjectFiles,
  putProjectFile,
  deleteProjectFile,
  clearProjectHistory,
  getSignals,
  getSessionSignals,
  getSessions,
  getProjectGraph,
  postTriggersPreview,
  postAddState,
  postAddSignal,
  postAddAction,
  putStateField,
  putActionField,
  putInitActionField,
  putSignalField,
  putActionOrder,
  deleteState,
  deleteProjectAction,
  deleteProjectSignal,
  getProjectRevision,
  getPublishPreview,
  postPublishProject,
  postRevertProject
} from '../api.js'
import { clearApiError, setApiError } from '../errorStore.js'
import ErrorBanner from './ErrorBanner.vue'
import { buildTimeline, highlightedStateKeyFor, nearestMessageIdAtOrBefore, resultingStateKeyFor, signalValuesFor } from '../benchmarkTimeline.js'
// Aliased: this file already uses "state" to mean an automaton state node
// — `liveState` is specifically the live conversation's current state,
// which this view's Inspector highlights as "current" (see the
// highlighted-state-key binding below).
import {
  state as liveState,
  messages,
  currentSessionId,
  draft,
  turnCount,
  autoTrackingEnabled,
  autoTrackingLoading,
  toggleAutoTracking,
  handleReset,
  handleSend,
  handleTruncateFrom,
  loadMessages,
  spokenTextEnabled,
  testModeProjectName
} from '../chatStore.js'

const props = defineProps({
  projectName: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['close', 'saved', 'download'])

const UPLOADABLE_PATTERN = /\.(txt|ya?ml)$/i

function lineIndent(line) {
  const m = line.match(/^[ \t]*/)
  return m ? m[0].length : 0
}

function isBlankOrComment(trimmed) {
  return !trimmed || trimmed.startsWith('#')
}

// Best-effort line lookup for a top-level block's direct child key (e.g.
// `states:` -> a state name, `signals:` -> a signal name), used to jump
// the editor's cursor to a definition clicked in the Inspect panel. A
// heuristic indentation scan, not a real YAML parse — round-tripping line
// numbers through the backend's yaml.safe_load would need a custom loader
// just for this — so it relies on this app's own consistent indentation
// (every project file it writes uses 2 spaces).
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

// Within stateKey's block, finds the line of the `attachments:` list item
// naming fileName — a plain scalar list (`- filename`), unlike actions'
// own `- name: ...` mappings (see findActionLine), so this only has to
// match the whole trimmed item text once unwrapped from its leading
// `- ` and optional quotes. Used to jump from clicking an attachment
// button in the state's own editable form to where it's actually
// declared, instead of opening the attachment file itself.
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

// The one action with no source state to search under (see
// InspectorGraphTab.vue's own pseudo-node/isInitEdge) — a bare top-level
// key, not a states: child, so findActionLine's own state-block scan
// doesn't apply here at all.
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
const uploadInput = ref(null)

// Whichever one of these is actually mounted right now (see the editor
// pane's own v-if/v-else, keyed off currentFileName === 'index.yml') —
// CodeEditor.vue/IndexYmlEditorView.vue each own their own loading/
// saving/isDirty state internally now (see activeEditorIsDirty below,
// the closest thing this view has to the old shared isDirty ref).
const codeEditorRef = ref(null)
const indexYmlEditorRef = ref(null)
const activeEditorIsDirty = computed(() =>
  currentFileName.value === 'index.yml' ? (indexYmlEditorRef.value?.isDirty ?? false) : (codeEditorRef.value?.isDirty ?? false)
)

// Inspect panel: the shared Inspector component (see Inspector.vue) shows
// the last-saved project's state graph, its signal definitions, and the
// metrics_framework's core metrics. Open by default, see toggleInspect.
// `inspectorRef` is how this view drives the few things that stay its
// own responsibility: reloading after a save, refreshing the Metrics
// tab, and resizing the graph on drag. The active AI model's own info
// used to be a tab here too — see ModelMenu.vue's own "(?)" button now.
const inspecting = ref(true)
const inspectorRef = ref(null)
const inspectorWidth = ref(360)
// The Graph/State-tab/Actions-tab shared selection ({kind, data} | null)
// — while editorOpen is on, index.yml's own dedicated graph (see
// IndexYmlEditorView.vue) is the one producing it instead of the
// Inspector's own "States" tab (which the tab set below drops entirely
// in that case) — same shape either way (see InspectorGraph.vue's own
// 'select' emit), so stateTabElement/actionsTabList below don't care
// which one it actually came from.
const selectedGraphElement = ref(null)

// Identifies whatever a state/action/signal add-button just created — a
// state/action/signal card matching this (see InspectorDetailCard.vue's
// own elementIdentity/InspectorSignalsTab.vue) plays a yellow-fade
// highlight so a brand new entry is obviously the one that just appeared,
// without any of the actual selection/tab-switch/scroll logic needing to
// know why. Same string shape as InspectorDetailCard.vue's own
// elementIdentity ('state:<key>' / 'action:<stateKey>/<actionName>'),
// plus 'signal:<name>' for InspectorSignalsTab.vue.
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
// belongs to (see InspectorGraph.vue's own edgeToCyData: matchStateKey).
// Not gated on index.yml actually being the open file: IndexYmlEditorView
// (and its own InspectorGraph, the thing stateTabElement/actionsTabList
// below actually resolve this against) now stays mounted regardless of
// which file the explorer has open (see this view's own template, v-show
// not v-if) precisely so a selection made in the Graph keeps resolving
// while the user is off looking at some attachment file — clearing it
// here just because a different file is open would silently undo that.
const selectedStateKey = computed(() => {
  if (!selectedGraphElement.value) return null
  return selectedGraphElement.value.kind === 'state'
    ? selectedGraphElement.value.data.id
    : selectedGraphElement.value.data.matchStateKey
})

// Resolved off index.yml's own already-loaded graph data (see
// IndexYmlEditorView.vue's own stateElementFor/actionsForState,
// delegating to InspectorGraph.vue) rather than a second fetch of their
// own — null/[] whenever nothing is selected, or the dedicated view
// isn't even mounted (editorOpen off, or some other file open).
const stateTabElement = computed(() => {
  const key = selectedStateKey.value
  return key == null ? null : (indexYmlEditorRef.value?.stateElementFor(key) ?? null)
})
// No selection at all falls back to the init-action — the one action of
// the automaton's own synthetic "" state (see InspectorGraph.vue's own
// edgeToCyData/isInitEdge) — rather than an empty list, so "Actions" always
// has something to show (see inspectorTabs below, no longer gated on a
// selection existing first).
const actionsTabList = computed(() => {
  const key = selectedStateKey.value ?? ''
  return indexYmlEditorRef.value?.actionsForState(key) ?? []
})

// The tab set this view's own Inspector shows — see Inspector.vue's own
// slot-based contract (BenchmarkProjectView.vue passes a different set,
// including Performance and excluding Env — see its own tabs). "State"/
// "Actions"/"Signals" are index.yml's own — index.yml's graph is what
// they're resolved against (see stateTabElement/actionsTabList) and what
// "Signals" reads state-key context off of, none of which means anything
// about an attachment file's own content. So while some *other* file is
// the one open, this collapses to a single "Info" tab (see this view's
// own #tab-info slot — just that file's own media type, off CodeEditor.
// vue's own mediaType) rather than leaving State/Actions/Signals up
// showing index.yml's own selection while an unrelated file fills the
// editor pane next to them. Metrics/Env are both live-conversation
// concepts (a metric run, the persisted env for *this* session) that
// don't apply while editing (see mode/highlightedStateKey's own "no
// current state in edit mode") — shown only in 'test'.
const inspectorTabs = computed(() => {
  if (mode.value === 'test') {
    return [
      { id: 'states', label: 'States' },
      { id: 'signals', label: 'Signals' },
      { id: 'metrics', label: 'Metrics' },
      { id: 'env', label: 'Env' }
    ]
  }
  if (currentFileName.value !== 'index.yml') {
    return [{ id: 'info', label: 'Info' }]
  }
  return [
    { id: 'state', label: 'State' },
    { id: 'actions', label: 'Actions' },
    { id: 'signals', label: 'Signals' }
  ]
})
const inspectorActiveTab = ref('states')

// A selection made in edit mode (a Graph tap, a jump-to-definition from
// elsewhere) drives the Inspector's own "State"/"Actions" tab, not just
// the graph itself — the user shouldn't have to also manually click over
// to whichever tab now has something to show (see InspectorActionsTab.
// vue's own scroll-into-view for the row-level half of "make the
// selection visible").
watch(selectedGraphElement, (element) => {
  if (mode.value !== 'edit' || !element) return
  inspectorActiveTab.value = element.kind === 'state' ? 'state' : 'actions'
})
// Live value/error per signal name — fed to the Inspector's signal-values
// prop, refreshed on its own cadence (see refreshSignalValues), never a
// concern the Inspector itself resolves (see Inspector.vue's own
// signalValues prop docstring).
const signalValueByName = ref({})

// The live session's own Signals event log and starting state — the same
// two ingredients BenchmarkProjectView.vue fetches for a *past* session,
// fetched here for the *current* one instead (see refreshSignalsLog/
// refreshSessionStartState) so this view's chat can show the exact same
// clickable message+transition timeline (see ChatTimeline.vue/
// benchmarkTimeline.js), just kept live instead of frozen. Independent of
// `inspecting` — the timeline itself is part of the chat panel, which can
// be open while the Inspect panel is closed.
const signalsLog = ref([])
const sessionStartState = ref(null)

// The point in time the Inspector reflects — null means "follow the live
// conversation as it happens" (the historical default), a value means
// "pinned to whatever was clicked in the timeline" (see selectMessage/
// selectTransition, both a toggle: clicking the same entry again clears
// this back to null/live). Reset on every session switch — a selection
// from the previous session's history means nothing in a new one.
const selected = ref(null)

// chatStore.js's live `messages` shaped like BenchmarkProjectView.vue's
// own rawMessages (id/timestamp/role/content/audio_text) — the common
// input shape buildTimeline (and every helper built on it) expects,
// regardless of whether the log being merged in is historical or live.
// The in-flight assistant placeholder (see chatStore.js's submitMessage)
// has no messageId yet — kept in, with `id: null`, so the streaming
// bubble still shows up in this timeline exactly as it does in the plain
// chat, just unable (harmlessly) to match any transition by id until it
// resolves.
const rawLiveMessages = computed(() =>
  messages.value.map((m) => ({
    id: m.messageId ?? null,
    timestamp: m.timestamp,
    role: m.role,
    content: m.content,
    audio_text: m.audioText
  }))
)

// includeSelfLoops: true — unlike BenchmarkProjectView.vue's own review
// timeline, the live chat here should show every action that actually
// fired, including a self-loop that left the state unchanged (see
// ChatTimeline.vue's own dimmed styling for these) — not just the ones
// an expert happened to annotate.
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
    const allSessions = await getSessions()
    sessionStartState.value = allSessions.find((s) => s.id === currentSessionId.value)?.start_state ?? null
  } catch {
    // already surfaced via apiFetch
  }
}

// The project's own current set of real state keys (see
// project_service.py's get_project_graph — nodes only, the reserved ""
// implicit state is never one of these) — refreshed after every save,
// since that's the only thing that can change it. Backs isStateGone
// below: restarting from a bubble whose own state has since been
// renamed/removed (see backend ProjectService._finalize_project_update,
// which now keeps the conversation alive across saves that don't touch
// its own current state) would have nowhere valid to land.
const validStateKeys = ref(new Set())

// Every real state's own {key, uiLabel} — the Actions tab's own target
// <select> options (see InspectorDetailCard.vue's availableStates prop),
// refreshed alongside validStateKeys since both come from the same
// getProjectGraph call and change together.
const availableStates = ref([])

// The live chat's own timeline (see ChatTimeline.vue's own
// resolveStateLabel prop) shows a transition's ui-label instead of its
// raw state key — always resolved against availableStates' own current
// draft (refreshed after every edit, see refreshValidStateKeys), so
// renaming a state's ui-label without touching its key (exactly what
// prompted this) is reflected here immediately, not just in the Graph.
// Falls back to the raw key itself for a transition landing on a state
// that's since been renamed/removed entirely (see isStateGone) — better
// than showing nothing.
function stateLabelFor(key) {
  return availableStates.value.find((s) => s.key === key)?.uiLabel ?? key
}

async function refreshValidStateKeys() {
  try {
    const { nodes } = await getProjectGraph(props.projectName)
    validStateKeys.value = new Set(nodes.map((n) => n.state.key))
    availableStates.value = nodes.map((n) => ({ key: n.state.key, uiLabel: n.state.ui_label }))
  } catch {
    // already surfaced via apiFetch
  }
}

// The state a given message's own turn left the conversation in — see
// resultingStateKeyFor's own docstring for why that's a different
// question than highlightedStateKeyFor's (used for the Inspector
// selection below), and thus a different function.
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

// See benchmarkTimeline.js for the actual logic behind each of these —
// same helpers BenchmarkProjectView.vue uses for its own selection, just
// falling back to the *live* current state/signals (rather than null)
// whenever nothing is selected, since there's always a live conversation
// here to fall back to. "Current state" only means anything in 'test'
// mode at all — 'edit' has no live conversation driving the graph (the
// embedded chat is closed there, see `mode`), so nothing is ever
// "current" while editing (the Inspector's own "Current" badge — see
// InspectorDetailCard.vue's isSelectedStateCurrent — simply never
// matches null).
const highlightedStateKey = computed(() => {
  if (mode.value !== 'test') return null
  return selected.value ? highlightedStateKeyFor(selected.value, timeline.value, sessionStartState.value) : (liveState.value?.key ?? null)
})

// old_state === '' (the automaton's own init transition) is a real,
// clickable edge in the graph too now — a transparent pseudo-node's own
// outgoing edge (see InspectorGraphTab.vue's isInitEdge) — so this no
// longer excludes it: every transition selection highlights *some* edge.
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

// The Env tab's own "is this still 'now'?" (see Inspector.vue's
// envEditable prop): true with nothing selected (the live view), and
// also true when the selected bubble is the conversation's own latest
// message — nothing happened after it yet, so it's effectively "now"
// too, unlike every earlier bubble which is genuine history.
const latestMessageId = computed(() => {
  const msgs = rawLiveMessages.value
  return msgs.length ? msgs[msgs.length - 1].id : null
})
const envEditable = computed(() =>
  !selected.value ||
  (selected.value.kind === 'message' && selected.value.message.id === latestMessageId.value)
)

const effectiveSignalValues = computed(() =>
  selected.value ? signalValuesFor(selected.value, signalsLog.value) : signalValueByName.value
)

// "Restart from here" (RestartFromHereButton.vue, this view's chat only —
// see ChatTimeline.vue's message-actions slot): both gestures truncate
// the conversation at this message's own timestamp first (see
// chatStore.js's handleTruncateFrom, which also rolls the live state
// back), then differ only in what happens to the message's own text —
// preloaded for the user to review/edit, or resent immediately as-is.
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

// A definition clicked in the Inspect panel (graph node/edge or signal
// block) to jump the editor's cursor to, once index.yml is the file open
// in the editor — see jumpToDefinition/applyPendingCursorTarget. Cleared
// once applied, or if the user cancels a pending file-switch dialog.
const pendingCursorTarget = ref(null)

// Single "Edit | Test" segmented control, replacing what used to be two
// independent Edit/Chat toggles (both could be open together, split
// vertically) — now mutually exclusive: 'edit' shows the file explorer +
// editor with the embedded chat closed, 'test' shows the embedded chat
// (full height, nothing to split against) with the editor closed. Also
// gates the Inspector's own tab set — see inspectorTabs above — and
// whether "current state" has any meaning at all (see highlightedStateKey
// below: only 'test' ever has a live conversation to be "current" in).
const mode = ref('edit')
const editorOpen = computed(() => mode.value === 'edit')
const chatOpen = computed(() => mode.value === 'test')

// Entering 'test' mode is this view's own chance to bootstrap a chat
// session against the draft — App.vue's own boot-time loadMessages() (the
// main app, always a real published-revision session) may well have
// already failed silently for a project that's never been published (see
// chatStore.js's loadMessages/handleReset, both of which read
// testModeProjectName internally now), leaving currentSessionId null;
// this is what lets this view's own embedded chat still work then,
// without the main app's live chat ever gaining the same ability. A
// no-op once a session already exists (e.g. a published project's own
// already-successful main-app bootstrap) — ensureSession itself already
// just touches/returns whichever one that is.
async function ensureDraftChatSession() {
  if (currentSessionId.value != null) return
  await loadMessages()
}

// testModeProjectName (see chatStore.js's own docstring) is this view's
// own signal to every session bootstrap/list/refresh function there
// (ensureSession, loadSessions, handleNewSession, ...) that "Test" mode's
// own separate session pool is in effect, not the real one — set the
// instant 'test' becomes the active mode, cleared the instant it isn't.
// Also cleared on unmount below, defensively: navigating away from this
// view entirely must never leave the main app's own chat silently
// resolving against this project's "Test" sessions afterward.
function setMode(next) {
  mode.value = next
  testModeProjectName.value = next === 'test' ? props.projectName : null
  if (next === 'test') ensureDraftChatSession()
}

onBeforeUnmount(() => { testModeProjectName.value = null })

// Whatever's queued behind the unsaved-changes dialog below — set
// whenever something that would discard the active editor's own dirty
// buffer (see activeEditorIsDirty) is requested while it's actually
// dirty: a file switch (see selectFile) or a structural edit (add/
// delete/field-edit on a state/action/signal, a reorder — see each
// handler below). `run` performs the action itself (already fully bound
// in its own closure); `label` is what the dialog shows the user (e.g.
// 'switch to "notes.txt"', 'add a new state'). Resolved one way or
// another by confirmPendingSave/Discard/Cancel.
const pendingAction = ref(null) // { run: () => void, label: string } | null

// Left panel width in px, adjusted by dragging the split divider.
const explorerWidth = ref(220)
// Which divider (if any) is currently being dragged — 'explorer' or
// 'inspector' — read by the single shared onDrag/stopDrag pair below.
let dragTarget = null

function activeEditor() {
  return currentFileName.value === 'index.yml' ? indexYmlEditorRef.value : codeEditorRef.value
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
// (see jumpToDefinition), once index.yml's own dedicated view has
// actually finished loading its own code buffer. Best-effort: a target
// that findStateLine/findActionLine/findSignalLine can't locate (e.g.
// hand-edited YAML with unusual indentation) just leaves the cursor
// where it was. IndexYmlEditorView's own jumpToLine already never
// switches the Graph/Code segment on this call's behalf (see its own
// docstring) — only moves the cursor while "code" is already showing —
// so there's nothing left for this to decide silent/non-silent about.
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
  else if (target.kind === 'attachment') lineIndex = findAttachmentLine(lines, target.stateKey, target.fileName)
  if (lineIndex === null) return
  indexYmlEditorRef.value?.jumpToLine(lineIndex)
}

// Entry point for the graph's node/edge taps, the Signals tab's rows, and
// the Inspector's own "State"/"Actions" tabs. index.yml is the only file
// definitions ever live in — if it isn't the one open, routes through
// the normal (possibly dialog-gated) file switch first; either way,
// waits for IndexYmlEditorView's own code buffer to actually have
// content before looking up a line in it (its own CodeEditor loads
// asynchronously on mount, unlike the old single shared editor this
// replaced, which was always already loaded whenever index.yml was
// already the open file). Never touches the Graph/Code segment itself —
// see applyPendingCursorTarget/IndexYmlEditorView's own jumpToLine, which
// only ever moves the cursor while "code" is already the segment
// showing, regardless of caller: that choice is the user's own, never
// something a click elsewhere should override on its behalf.
//
// `silent`: whether this may switch the open *file* to index.yml on its
// own — a plain row selection in the Inspector's own State/Actions/
// Signals tabs shouldn't yank the user out of whatever file/attachment
// they're actually looking at just to move a cursor they can't even see;
// this does nothing at all there instead. A direct Graph tap, or an
// explicit "show me its definition" action, still forces the file switch
// (the default) — locating the definition is the whole point of the click.
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
  // selectedGraphElement is deliberately left alone here — IndexYmlEditor
  // View stays mounted regardless of which file this switches to (see
  // this view's own template), so the Inspector's "State"/"Actions"
  // selection stays just as valid while browsing an attachment file as it
  // was before switching to it.
}

// Every entry point that would discard unsaved code — switching files in
// the Explorer, or any structural edit (add/delete/field-edit on a
// state/action/signal, a reorder) — routes through here instead of
// running `run` directly: dirty means ask first (queuing `run` behind
// the dialog below, see pendingAction), clean means there's nothing to
// lose, so it just runs immediately.
function guardedAction(label, run) {
  if (!activeEditorIsDirty.value) {
    run()
    return
  }
  pendingAction.value = { label, run }
}

// Entry point for both explorer clicks and post-upload auto-open.
function selectFile(fileName) {
  if (fileName === currentFileName.value) return
  guardedAction(`switch to "${fileName}"`, () => switchFile(fileName))
}

async function confirmPendingSave() {
  const action = pendingAction.value
  pendingAction.value = null
  if (await activeEditor()?.save?.()) action.run()
}

function confirmPendingDiscard() {
  const action = pendingAction.value
  pendingAction.value = null
  // The whole point of "Discard": the active editor's own dirty buffer
  // (whichever file it's currently showing — index.yml's embedded one,
  // or a plain attachment's) actually reverts to its last-loaded
  // content, not just "the dialog goes away while the edit lingers."
  activeEditor()?.discard?.()
  action.run()
}

function confirmPendingCancel() {
  pendingAction.value = null
  // A cursor jump that triggered this action is moot once it's declined
  // — don't let it fire on some later, unrelated action.
  pendingCursorTarget.value = null
}

// Common tail for every Add/edit/delete/reorder handler below: the
// backend already persisted the change (through put_project_file, same
// as a normal Save), so both index.yml's own dedicated view (graph +
// code buffer) and the Inspector need to catch up — same two refreshes
// a normal Save already triggers (see handleFileSaved).
async function refreshAfterProjectEdit() {
  await indexYmlEditorRef.value?.refresh(false)
  await indexYmlEditorRef.value?.reloadCode()
  if (inspecting.value) await inspectorRef.value?.refresh()
  refreshValidStateKeys()
  refreshProjectRevision()
}

// {revision, published_revision} — null while not yet loaded. A save can
// fork (see Db.save_project_files' own fork-on-first-edit-after-publish),
// bumping `revision` — refreshed after every save (see handleFileSaved/
// refreshAfterProjectEdit) and after every publish, never assumed stable
// across either.
const projectRevision = ref(null)
const publishing = ref(false)
// Set only while ProjectService.preview_publish reported needs_remap —
// the modal below is shown exactly while this is non-null. Cleared on
// both confirm and cancel.
const publishRemapPrompt = ref(null)
const publishRemapChoice = ref('')

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

async function handlePublish() {
  if (publishUpToDate.value || publishing.value) return
  publishing.value = true
  try {
    const preview = await getPublishPreview(props.projectName)
    if (preview.needs_remap) {
      publishRemapChoice.value = ''
      publishRemapPrompt.value = preview
      return
    }
    // Only ask when it's actually consequential — a live conversation
    // still running on the currently published revision (see
    // ProjectService.preview_publish's own has_active_sessions). Nobody
    // mid-conversation on it means nothing to warn about.
    if (
      preview.has_active_sessions &&
      !window.confirm(`Publish revision ${projectRevision.value?.revision}? There's an active session on the currently published revision — it will stay frozen there; this one becomes the new one.`)
    ) {
      return
    }
    projectRevision.value = await postPublishProject(props.projectName)
  } catch {
    // already surfaced via apiFetch
  } finally {
    publishing.value = false
  }
}

async function confirmPublishRemap(stateKey) {
  publishing.value = true
  try {
    projectRevision.value = await postPublishProject(props.projectName, stateKey)
    publishRemapPrompt.value = null
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
}

// The "Rev. X" split button's own dropdown arrow — only ever rendered
// (see the template below) when there's both a draft ahead of the
// published revision and a prior publication to revert to in the first
// place; a stale open flag surviving past that (revision info reloading
// out from under it) is harmless, the arrow/dropdown just won't be there
// to click.
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
  if (
    !window.confirm(
      `Revert to rev. ${targetRevision}? This permanently discards every unpublished change on rev. ${projectRevision.value.revision} — there's no undo for this.`
    )
  ) {
    return
  }
  publishing.value = true
  try {
    await postRevertProject(props.projectName)
    selectedGraphElement.value = null
    await refreshAfterProjectEdit()
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

function handleAddAction() {
  const stateKey = selectedStateKey.value
  if (!stateKey) return
  guardedAction('add a new action', async () => {
    try {
      const action = await postAddAction(props.projectName, stateKey)
      await refreshAfterProjectEdit()
      // The new action itself, not its containing state — selecting the
      // state here used to flip the Inspector's own active tab back to
      // "State" (see the selectedGraphElement watch above), which is
      // exactly the "view resets" bug this was reported as.
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

function handleSetActionField(stateName, actionName, field, value) {
  guardedAction(`edit "${field}"`, async () => {
    try {
      // The init-action (stateName '' — see InspectorGraph.vue's own
      // isInitEdge) lives outside `states:` entirely, so putActionField's
      // own state/action lookup can't reach it — every one of its own
      // editable fields (target, ui-label, ...) goes through the
      // dedicated endpoint instead.
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
      // Only a ui-label edit can rename the signal (see AutomatonYamlEditor.
      // set_signal_field's own ui-label special case) — state/action never
      // move for a field edit, since their own name/key never changes, but
      // a renamed signal's own line in the YAML does: reuse the same
      // cursor-repositioning heuristic a direct jump already uses, off the
      // *new* name the response just reported.
      if (field === 'ui-label') await jumpToDefinition({ kind: 'signal', signalName: signal.name }, { silent: true })
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

// Common tail for a successful Save on either kind of editor (see
// CodeEditor.vue/IndexYmlEditorView.vue's own 'saved' emit) — re-emitted
// up (see App.vue's own listener) plus the same two refreshes a
// structural edit's own refreshAfterProjectEdit triggers, minus
// reloading the buffer that was just the one doing the saving.
async function handleFileSaved() {
  emit('saved')
  // A manual Save from the Code segment writes index.yml directly too
  // (same file, same endpoint as every structural edit) — the Graph
  // segment's own cytoscape data needs the same refresh a structural
  // edit already triggers (see refreshAfterProjectEdit), or it just
  // keeps showing whatever it last had until the user happens to switch
  // segments away and back. A no-op via optional chaining when a
  // *different* file was the one just saved (nothing to refresh — an
  // attachment save never touches index.yml's own graph).
  await indexYmlEditorRef.value?.refresh(false)
  if (inspecting.value) await inspectorRef.value?.refresh()
  refreshValidStateKeys()
  refreshProjectRevision()
}

// The Inspector's own "State"/"Actions" tabs share the exact same
// selection the Graph itself drives (see selectedGraphElement above),
// but unlike a direct Graph click (see InspectorGraph.vue's own
// handleNodeTap/handleEdgeTap, which already emit jump-to-definition
// separately alongside their own 'select') a row click here only ever
// emits the one event — this is the tab-side equivalent of both at once.
// Silent (see jumpToDefinition's own `silent` option): selecting a row
// here shouldn't yank the user into the Code segment if they're looking
// at the Graph — only the cursor moves, and only if Code is already
// showing.
function handleTabSelect(element) {
  selectedGraphElement.value = element
  if (!element) return
  if (element.kind === 'state') jumpToDefinition({ kind: 'state', stateKey: element.data.id }, { silent: true })
  else jumpToDefinition(
    { kind: 'action', stateKey: element.data.matchStateKey, actionName: element.data.actionName },
    { silent: true }
  )
}

// The state edit form's own attachment buttons (see InspectorDetailCard.
// vue's own selectAttachment) jump to where the file is declared in
// index.yml rather than opening it (unlike every other attachment button
// elsewhere in the app, still routed through selectFile). Silent, same
// as every other edit-mode jump — the Graph/Code segmented control is
// never switched automatically, only the user's own click on it does that.
function handleJumpToAttachment(fileName) {
  const stateKey = stateTabElement.value?.data.id
  if (stateKey == null) return
  jumpToDefinition({ kind: 'attachment', stateKey, fileName }, { silent: true })
}

function triggerUpload() {
  uploadInput.value?.click()
}

async function handleUploadFile(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // reset so re-selecting the same file re-fires change
  if (!file) return
  if (!UPLOADABLE_PATTERN.test(file.name)) {
    setApiError('Only .txt or .yml/.yaml files can be uploaded.')
    return
  }
  uploading.value = true
  clearApiError()
  try {
    const text = await file.text()
    await putProjectFile(props.projectName, file.name, text)
    await loadFiles()
    await selectFile(file.name)
  } catch {
    // already surfaced via apiFetch
  } finally {
    uploading.value = false
  }
}

async function handleNewFile() {
  // .yml/.yaml is technically accepted (see UPLOADABLE_PATTERN) but never
  // a sensible choice here — index.yml is the only YAML file the
  // automaton itself ever reads, so a second one could only ever be an
  // inert attachment — the example below steers toward what an
  // attachment is actually for instead.
  const rawName = window.prompt('New file name (e.g. notes.txt):')
  if (rawName === null) return // cancelled
  const name = rawName.trim()
  if (!name) return
  if (!UPLOADABLE_PATTERN.test(name)) {
    setApiError('Only .txt or .yml/.yaml files can be created.')
    return
  }
  if (files.value.includes(name)) {
    setApiError(`A file named "${name}" already exists.`)
    return
  }
  creatingFile.value = true
  clearApiError()
  try {
    await putProjectFile(props.projectName, name, '')
    await loadFiles()
    await selectFile(name)
  } catch {
    // already surfaced via apiFetch
  } finally {
    creatingFile.value = false
  }
}

// index.yml is protected server-side too (delete_project_file rejects it) —
// the button is also hidden for it in the template, this is just a second
// guard against a stale click.
async function handleDeleteFile(fileName) {
  if (fileName === 'index.yml') return
  if (!window.confirm(`Delete file "${fileName}"? This cannot be undone.`)) return
  deletingFile.value = fileName
  clearApiError()
  try {
    await deleteProjectFile(props.projectName, fileName)
    await loadFiles()
    if (fileName === currentFileName.value) await switchFile('index.yml')
  } catch {
    // already surfaced via apiFetch
  } finally {
    deletingFile.value = null
  }
}

// Only prompts when there's actually something to lose — a clean editor
// (nothing typed, or already saved) closes straight away. Undo/redo
// history itself is cleared on entry, not here — see onMounted.
function handleClose() {
  if (activeEditorIsDirty.value && !window.confirm('Discard unsaved changes to this file?')) return
  emit('close')
}

// Live values for whatever signals the active conversation currently has
// (see chatStore.js's shared state — the same getSignals() ChatWindow's
// own Signals concerns already rely on via SignalsView). Just the values —
// the flash-on-change animation is Inspector.vue's own concern (it watches
// this prop internally, see its signalValues prop docstring).
async function refreshSignalValues() {
  try {
    const nextValues = await getSignals()
    signalValueByName.value = Object.fromEntries(nextValues.map((s) => [s.name, { value: s.value, error: s.error }]))
  } catch {
    // already surfaced via apiFetch
  }
}

// { stateKey, actionName } of the action the engine would fire next from
// the live current state, or null — see refreshNextAction. Reuses
// postTriggersPreview (already computed for SignalsView's own "Next
// triggerable action" section) instead of re-deriving trigger evaluation
// here. Fed to the Inspector as its next-action-edge prop, which draws and
// highlights the corresponding edge itself.
const nextAction = ref(null)

// Reuses the same triggers-preview endpoint SignalsView already calls for
// its own "Next triggerable action" section — no separate client-side
// reimplementation of trigger evaluation. would_fire's own FIFO-priority
// logic (see backend Automaton.preview_triggers) decides the winner; this
// just finds it.
async function refreshNextAction() {
  const stateKeyAtFetch = liveState.value?.key
  if (stateKeyAtFetch == null) {
    nextAction.value = null
    return
  }
  try {
    const signalsList = await getSignals()
    const signalValues = Object.fromEntries(signalsList.map((s) => [s.name, s.error ? null : s.value]))
    const previews = await postTriggersPreview(signalValues)
    const winner = previews.find((p) => p.would_fire)
    nextAction.value = winner ? { stateKey: stateKeyAtFetch, actionName: winner.action_name } : null
  } catch {
    // already surfaced via apiFetch — the graph just shows no "next" edge
    nextAction.value = null
  }
}

// Shared by the initial mount (Inspect is open by default) and every
// later re-expand (see handleInspectorCollapsedChange). Inspector.vue
// loads its own graph/signals definitions on mount (see its own
// onMounted, always-mounted now — no more v-if teardown/remount) — this
// view only owns the live/point-in-time pieces layered on top of them.
async function openInspect() {
  await nextTick()
  await Promise.all([refreshNextAction(), refreshSignalValues()])
}

// Inspector.vue's own collapse toggle (see its own header) drives this —
// no more separate toolbar button/× close pair (see this view's own
// header, which no longer has an "Inspect" button at all).
function handleInspectorCollapsedChange(collapsed) {
  inspecting.value = !collapsed
  if (inspecting.value) openInspect()
}

function handleDownload() {
  // App.vue's own handleModelDownload does the actual download and shows
  // the "downloaded" notice itself, once it's actually finished — not
  // here, and not before it's even started.
  emit('download', props.projectName)
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
// next even without a state change — see chatStore.js's turnCount. Metrics
// are heavier to compute, so unlike signals they're only refreshed while
// the Inspector's own Metrics tab is the one actually open (see
// InspectorMetricsTab.vue's own refresh(active)) — never prefetched in
// the background. signalsLog, unlike those, feeds the chat timeline
// itself (transition rows, annotation icons) — visible whenever the chat
// panel is, whether or not Inspect is open, so it refreshes
// unconditionally (see InspectorSignalsTab.vue's own refresh()).
watch(turnCount, () => {
  // A completed turn always adds a new message — whatever was selected
  // before now belongs to older history, so the Inspector should follow
  // the conversation's own newest message again (same reasoning as the
  // currentSessionId watch below going back to null on a session switch),
  // rather than staying pinned on a bubble that's no longer the latest.
  selected.value = null
  refreshSignalsLog()
  if (!inspecting.value) return
  refreshNextAction()
  refreshSignalValues()
  inspectorRef.value?.refresh()
  // The Inspector's own "States" tab (and its own graph) only exists
  // while editorOpen is off (see inspectorTabs) — while it's on,
  // index.yml's own dedicated view holds the one graph there is, so
  // it's the one that needs the same nudge instead.
  if (editorOpen.value) indexYmlEditorRef.value?.refresh(false)
})

// Metrics aren't reactive to a prop change on their own (see
// InspectorMetricsTab.vue's own refresh(active) docstring) — a selection
// change needs its own explicit nudge, same as BenchmarkProjectView.vue's
// own watch(selected). Env gets the same nudge: it isn't prop-driven
// either (it's fetched straight from the db, see InspectorEnvTab.vue's
// own loadEnv), so switching which message is selected wouldn't
// otherwise re-pull it.
watch(selected, () => {
  if (!inspecting.value) return
  nextTick(() => {
    inspectorRef.value?.refresh()
  })
})

// A session switch (from this view's own Sessions button, or the main
// page's — currentSessionId is shared, see chatStore.js's selectSession)
// always shows *that* session's own timeline from scratch — whatever was
// selected, or logged, belonged to a different session's history.
watch(currentSessionId, () => {
  selected.value = null
  refreshSessionStartState()
  refreshSignalsLog()
})

// Gates mounting CodeEditor/IndexYmlEditorView in the template below —
// each one loads its own content as soon as it mounts, so without this
// they could start (and finish) that fetch concurrently with, or even
// before, the clearProjectHistory call two lines down, leaving their
// very first can_undo/can_redo reflecting pre-clear history instead of
// the fresh slate a just-opened editing session is supposed to start
// from (see the old, single-shared-editor code this replaced, which
// sequenced the same two calls explicitly for exactly this reason).
const historyCleared = ref(false)

onMounted(async () => {
  loadFiles()
  refreshSessionStartState()
  refreshSignalsLog()
  refreshValidStateKeys()
  refreshProjectRevision()
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
          >Edit</button>
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
      <div class="edit-project-main-column">
        <div v-show="editorOpen" class="edit-project-top-row">
          <div class="file-explorer" :style="{ width: explorerWidth + 'px' }">
            <div class="file-explorer-header">
              <span class="file-explorer-title">Files</span>
              <div class="file-explorer-header-actions">
                <button class="file-explorer-icon-btn" :disabled="uploading" title="Upload file" @click="triggerUpload">
                  <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                    <path d="M12 3l4 4h-3v6h-2V7H8l4-4zM5 19v-6h2v6h10v-6h2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z" />
                  </svg>
                </button>
                <button class="file-explorer-icon-btn" :disabled="creatingFile" title="New file" @click="handleNewFile">+</button>
              </div>
              <input
                ref="uploadInput"
                type="file"
                accept=".txt,.yml,.yaml"
                class="file-explorer-upload-input"
                @change="handleUploadFile"
              />
            </div>
            <p v-if="filesLoading" class="file-explorer-status">Loading…</p>
            <ul v-else class="file-explorer-list">
              <li v-for="name in files" :key="name" class="file-explorer-row">
                <button
                  class="file-explorer-item"
                  :class="{ 'file-explorer-item-active': name === currentFileName }"
                  :title="name"
                  @click="selectFile(name)"
                >
                  {{ name }}
                </button>
                <button
                  v-if="name !== 'index.yml'"
                  class="file-explorer-delete-btn"
                  :disabled="deletingFile === name"
                  title="Delete file"
                  @click="handleDeleteFile(name)"
                >
                  ×
                </button>
              </li>
            </ul>
            <button class="file-explorer-download-btn" @click="handleDownload">Download project</button>
          </div>

          <div class="split-divider" @mousedown="startExplorerDrag"></div>

          <div class="edit-project-editor-pane">
            <p v-if="!historyCleared" class="edit-project-status">Loading…</p>
            <template v-else>
              <!-- Stays mounted (v-show, not v-if) even while a different
                   file is open — its own InspectorGraph is the one and
                   only place the Inspector's "State"/"Actions" selection
                   is resolved from (see stateTabElement/actionsTabList),
                   so unmounting it just because an attachment is being
                   viewed used to blank the Inspector out from under a
                   selection that's still perfectly valid. Same v-show
                   (not v-if) pattern this view's own Graph/Code segments
                   already use internally, for the same reason. -->
              <IndexYmlEditorView
                v-show="currentFileName === 'index.yml'"
                ref="indexYmlEditorRef"
                :project-name="projectName"
                :highlighted-state-key="highlightedStateKey"
                :auto-jump-on-highlight-change="true"
                :next-action-edge="selected ? null : nextAction"
                :fired-action-edge="firedActionEdge"
                @jump-to-definition="(target) => jumpToDefinition(target, { silent: true })"
                @select="selectedGraphElement = $event"
                @saved="handleFileSaved"
              />
              <div v-if="currentFileName !== 'index.yml'" class="edit-project-editor-attachment">
                <div class="edit-project-editor-toolbar">
                  <span class="edit-project-editor-filename">{{ currentFileName }}</span>
                  <div class="edit-project-editor-toolbar-actions">
                    <button
                      class="undo-redo-btn"
                      title="Undo"
                      :disabled="codeEditorRef?.loading || codeEditorRef?.saving || !codeEditorRef?.canUndo"
                      @click="codeEditorRef?.undo()"
                    >↶</button>
                    <button
                      class="undo-redo-btn"
                      title="Redo"
                      :disabled="codeEditorRef?.loading || codeEditorRef?.saving || !codeEditorRef?.canRedo"
                      @click="codeEditorRef?.redo()"
                    >↷</button>
                    <button
                      class="save-btn"
                      :disabled="codeEditorRef?.loading || codeEditorRef?.saving || !codeEditorRef?.isDirty"
                      @click="codeEditorRef?.save()"
                    >{{ codeEditorRef?.saving ? 'Saving…' : 'Save' }}</button>
                  </div>
                </div>
                <div class="edit-project-editor-content">
                  <CodeEditor
                    :key="currentFileName"
                    ref="codeEditorRef"
                    :project-name="projectName"
                    :file-name="currentFileName"
                    @saved="handleFileSaved"
                  />
                </div>
              </div>
            </template>
          </div>
        </div>

        <Transition name="panel-slide-bottom">
        <div v-if="chatOpen" class="edit-project-chat-wrap edit-project-chat-wrap-full">
          <div class="edit-project-chat-panel">
            <div class="edit-project-chat-toolbar">
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
                Dev mode: freeze automatic state transitions
              </label>
              <div class="edit-project-chat-toolbar-actions">
                <button class="reset-btn" @click="handleReset()">Reset</button>
                <ModelMenu />
              </div>
            </div>
            <ChatWindow allow-import>
              <template #timeline>
                <ChatTimeline
                  :timeline="timeline"
                  :signals-log="signalsLog"
                  :selected="selected"
                  :spoken-text-enabled="spokenTextEnabled"
                  :resolve-state-label="stateLabelFor"
                  @select-message="selectMessage"
                  @select-transition="selectTransition"
                >
                  <template #message-actions="{ message }">
                    <RestartFromHereButton
                      v-if="message.role === 'user'"
                      :disabled="isStateGone(message)"
                      @long-press="restartAndPrefill(message)"
                      @double-click="restartAndResend(message)"
                    />
                  </template>
                </ChatTimeline>
              </template>
            </ChatWindow>
          </div>
        </div>
        </Transition>
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
              <InspectorGraphTab
                :ref="registerTab('states')"
                :project-name="projectName"
                :highlighted-state-key="highlightedStateKey"
                :auto-jump-on-highlight-change="true"
                :next-action-edge="selected ? null : nextAction"
                :fired-action-edge="firedActionEdge"
                :editable-files="files"
                @jump-to-definition="jumpToDefinition"
                @select-attachment="selectFile"
              />
            </template>
            <template #tab-state="{ registerTab }">
              <InspectorStateTab
                :ref="registerTab('state')"
                :selected-element="stateTabElement"
                :editable-files="files"
                :highlighted-state-key="highlightedStateKey"
                :recently-added-key="recentlyAddedKey"
                @select="handleTabSelect"
                @select-attachment="selectFile"
                @jump-to-attachment="handleJumpToAttachment"
                @set-field="(field, value) => handleSetStateField(stateTabElement?.data.id, field, value)"
                @delete="handleDeleteState"
                @add-state="handleAddState"
              />
            </template>
            <template #tab-actions="{ registerTab }">
              <InspectorActionsTab
                :ref="registerTab('actions')"
                :actions="actionsTabList"
                :editable-files="files"
                :selected-element="selectedGraphElement"
                :next-action-edge="selected ? null : nextAction"
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
            <template #tab-info>
              <p v-if="codeEditorRef?.mediaType" class="inspector-info-tab-mediatype">type: {{ codeEditorRef.mediaType }}</p>
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
              <InspectorMetricsTab :ref="registerTab('metrics')" :until-message-id="untilMessageId" />
            </template>
            <template #tab-env="{ registerTab }">
              <InspectorEnvTab :ref="registerTab('env')" :until-message-id="untilMessageId" :editable="envEditable" />
            </template>
          </Inspector>
        </div>
      </div>
    </div>

    <div v-if="pendingAction" class="switch-dialog-overlay">
      <div class="switch-dialog">
        <p>"{{ currentFileName }}" has unsaved changes. Save before you {{ pendingAction.label }}?</p>
        <div class="switch-dialog-actions">
          <button class="switch-dialog-save-btn" :disabled="activeEditor()?.saving" @click="confirmPendingSave">Save</button>
          <button class="switch-dialog-discard-btn" :disabled="activeEditor()?.saving" @click="confirmPendingDiscard">Discard</button>
          <button class="switch-dialog-cancel-btn" :disabled="activeEditor()?.saving" @click="confirmPendingCancel">Cancel</button>
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

.save-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  cursor: pointer;
}

.save-btn:hover:not(:disabled) {
  background: #256428;
}

.save-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
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

.edit-project-main-column {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.edit-project-top-row {
  flex: 1;
  display: flex;
  min-height: 0;
}

.edit-project-chat-wrap {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

/* Always full — 'edit'/'test' are mutually exclusive (see `mode`), so
   whenever the chat is showing at all, the top-row (explorer/editor)
   isn't, and this fills the whole column instead of splitting against it. */
.edit-project-chat-wrap-full {
  flex: 1;
}

.edit-project-chat-wrap-full .edit-project-chat-panel {
  flex: 1;
  min-height: 0;
}

.panel-slide-bottom-enter-active,
.panel-slide-bottom-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.panel-slide-bottom-enter-from,
.panel-slide-bottom-leave-to {
  opacity: 0;
  transform: translateY(16px);
}

.edit-project-chat-panel {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-width: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.edit-project-chat-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #f5f5f7;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

.edit-project-chat-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.edit-project-chat-toolbar-actions .reset-btn {
  padding: 0.35rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  font-size: 0.85rem;
  cursor: pointer;
}

.edit-project-chat-toolbar-actions .reset-btn:hover {
  background: #c62828;
  color: white;
}

.file-explorer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-width: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.file-explorer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.4rem;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid #ddd;
  background: #f7f8fa;
}

.file-explorer-header-actions {
  display: flex;
  gap: 0.4rem;
}

.file-explorer-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.file-explorer-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  padding: 0;
  font-size: 0.9rem;
  line-height: 1;
}

.file-explorer-icon-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.file-explorer-icon-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.file-explorer-download-btn {
  flex-shrink: 0;
  width: 100%;
  padding: 0.5rem;
  border: none;
  border-top: 1px solid #ddd;
  border-radius: 0;
  background: #f7f8fa;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 600;
}

.file-explorer-download-btn:hover {
  background: #eef2f9;
}

.file-explorer-upload-input {
  display: none;
}

.file-explorer-status {
  margin: 0;
  padding: 0.6rem;
  font-size: 0.85rem;
  color: #444;
}

.file-explorer-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem;
  overflow-y: auto;
  flex: 1;
}

.file-explorer-row {
  display: flex;
  align-items: center;
  gap: 0.2rem;
}

.file-explorer-item {
  flex: 1;
  min-width: 0;
  display: block;
  text-align: left;
  padding: 0.4rem 0.5rem;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  color: #333;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.file-explorer-item:hover {
  background: #f0f4fa;
}

.file-explorer-item-active {
  background: #e4ecf9;
  color: #2c4d7a;
  font-weight: 600;
}

.file-explorer-delete-btn {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #c62828;
  cursor: pointer;
  font-size: 1rem;
}

.file-explorer-delete-btn:hover:not(:disabled) {
  background: #fdecea;
}

.file-explorer-delete-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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

.edit-project-editor-pane {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

/* Same shape as IndexYmlEditorView's own root .index-yml-editor — the two
   fill this same pane, one hidden via v-show while the other's showing
   (see the template above). */
.edit-project-editor-attachment {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.edit-project-editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #f5f5f7;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

.edit-project-editor-filename {
  min-width: 0;
  font-size: 0.85rem;
  font-weight: 600;
  color: #333;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.edit-project-editor-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-shrink: 0;
}

.undo-redo-btn {
  width: 1.8rem;
  height: 1.8rem;
  line-height: 1;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 1rem;
}

.undo-redo-btn:hover:not(:disabled) {
  background: #eef2f9;
}

.undo-redo-btn:disabled {
  border-color: #ccc;
  color: #ccc;
  cursor: not-allowed;
}

.edit-project-editor-content {
  flex: 1;
  min-height: 0;
  display: flex;
}

.inspector-info-tab-mediatype {
  margin: 0;
  font-size: 0.8rem;
  color: #444;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.edit-project-status {
  margin: auto;
  color: #444;
}

.edit-project-editor {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.edit-project-editor :deep(.cm-editor) {
  height: 100%;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.85rem;
}

.edit-project-editor :deep(.cm-scroller) {
  overflow: auto;
  line-height: 1.5;
}

.edit-project-editor :deep(.cm-editor.cm-focused) {
  outline: none;
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

.dev-mode-toggle {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.82rem;
  color: #666;
  cursor: pointer;
  user-select: none;
}

.dev-mode-toggle input {
  cursor: pointer;
}

.dev-mode-toggle-active {
  /* Same amber used elsewhere for "this changes normal behavior, pay
     attention" (see .inspector-detail-badge-current) — freezing
     transitions is a deliberate, temporary override, not the default. */
  color: #b06a00;
  font-weight: 600;
}

.dev-mode-toggle-disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.dev-mode-toggle-disabled input {
  cursor: not-allowed;
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

.switch-dialog-discard-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
  font-size: 0.85rem;
}

.switch-dialog-discard-btn:disabled {
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
