<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { keymap } from '@codemirror/view'
import { yaml } from '@codemirror/lang-yaml'
import ChatWindow from './chat/ChatWindow.vue'
import ChatTimeline from './chat/ChatTimeline.vue'
import RestartFromHereButton from './chat/RestartFromHereButton.vue'
import ModelMenu from './ModelMenu.vue'
import Inspector from './inspector/Inspector.vue'
import {
  getProjectFiles,
  getProjectFile,
  putProjectFile,
  undoProjectFile,
  redoProjectFile,
  deleteProjectFile,
  clearProjectHistory,
  getSignals,
  getSessionSignals,
  getSessions,
  getProjectGraph,
  postTriggersPreview
} from '../api.js'
import { clearApiError, setApiError } from '../errorStore.js'
import ErrorBanner from './ErrorBanner.vue'
import { buildTimeline, highlightedStateKeyFor, nearestMessageIdAtOrBefore, signalValuesFor } from '../benchmarkTimeline.js'
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
  sessionsPanelOpen,
  toggleSessionsPanel,
  spokenTextEnabled
} from '../chatStore.js'

const props = defineProps({
  projectName: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['close', 'saved', 'download'])

const UPLOADABLE_PATTERN = /\.(txt|ya?ml)$/i
const YAML_PATTERN = /\.ya?ml$/i

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

const loading = ref(true)
const saving = ref(false)
const uploading = ref(false)
const creatingFile = ref(false)
const deletingFile = ref(null)
const editorHost = ref(null)
const uploadInput = ref(null)

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

async function refreshValidStateKeys() {
  try {
    const { nodes } = await getProjectGraph(props.projectName)
    validStateKeys.value = new Set(nodes.map((n) => n.key))
  } catch {
    // already surfaced via apiFetch
  }
}

// The state a given message's own turn left the conversation in — same
// resolution BenchmarkProjectView.vue/this view's own Inspector selection
// already uses (see highlightedStateKeyFor), just evaluated per-bubble
// instead of only for whatever's currently selected.
function isStateGone(message) {
  const stateKey = highlightedStateKeyFor({ kind: 'message', message }, timeline.value, sessionStartState.value)
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
// here to fall back to.
const highlightedStateKey = computed(() =>
  selected.value ? highlightedStateKeyFor(selected.value, timeline.value, sessionStartState.value) : (liveState.value?.key ?? null)
)

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

// Embedded chat: a second view of the exact same conversation as the main
// app's (see chatStore.js) — docked below the file explorer/editor, with
// its own minimal toolbar (just Reset). Open by default, toggled like
// Inspect. Height in px, adjusted by dragging the horizontal divider above it.
const chatOpen = ref(true)
const chatHeight = ref(280)

// File explorer + editor row: v-show, not v-if (see toggleEditor) — the
// CodeMirror view is mounted once, imperatively, straight into
// editorHost.value (see mountEditor's own `parent:` below); an v-if here
// would tear that DOM node down and leave the EditorView pointing at a
// detached node on the next open, exactly the same reason editorHost's
// own v-show (below) exists. Open by default, toggled like Chat/Inspect.
const editorOpen = ref(true)

// The editor's own doc is the source of truth for content while it's
// mounted — this ref only mirrors it (via the updateListener below) so
// save() has something to send without querying the view directly.
const content = ref('')
// What was last loaded/saved for the current file — compared against
// `content` to decide whether switching/closing needs a confirmation.
const originalContent = ref('')
const isDirty = computed(() => content.value !== originalContent.value)

// Whether the currently open file's Undo/Redo buttons are enabled — the
// backend decides this (see db.Db.has_undo/has_redo, scoped to the
// current user), refreshed from its response on every load/save/undo/redo.
// The frontend never tracks version numbers or navigates by them: it just
// asks for undo/redo and the backend returns whatever content that yields.
const canUndo = ref(false)
const canRedo = ref(false)

// Set while the unsaved-changes dialog is blocking a switch to this file —
// resolved one way or another by confirmSwitchSave/Discard/Cancel.
const pendingFileName = ref(null)

// Left panel width in px, adjusted by dragging the split divider.
const explorerWidth = ref(220)
// Which divider (if any) is currently being dragged — 'explorer' or
// 'inspector' — read by the single shared onDrag/stopDrag pair below.
let dragTarget = null

let view = null
const editableCompartment = new Compartment()

// Bumped by every loadFileContent/applyHistoryNavigation call, each of
// which captures its own value at the start and checks it again after its
// own await — whichever such call was the *last* one started always wins.
// Without this, clicking a different file (or Undo/Redo) again while a
// previous fetch for the old one is still in flight let both eventually
// call createEditor(), each appending its own EditorView into
// editorHost.value (the constructor never clears the parent — see
// @codemirror/view's own EditorView), leaving two live, DOM-attached
// editors at once; or let a stale undo/redo response overwrite the
// now-current file's content/undo-redo state after the fact.
let requestToken = 0

function createEditor(doc, fileName) {
  const extensions = [
    basicSetup,
    EditorView.lineWrapping,
    editableCompartment.of(EditorView.editable.of(true)),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) content.value = update.state.doc.toString()
    }),
    // Ctrl-S (Cmd-S on Mac, via CodeMirror's own "Mod-" alias) — same
    // guard as the toolbar's Save button (:disabled), and always swallows
    // the key itself so the browser's native "Save page as" never opens,
    // even when there's nothing to save.
    keymap.of([
      {
        key: 'Mod-s',
        run: () => {
          if (!loading.value && !saving.value && isDirty.value) saveCurrentFile()
          return true
        }
      }
    ])
  ]
  if (YAML_PATTERN.test(fileName)) extensions.splice(1, 0, yaml())
  view = new EditorView({ doc, extensions, parent: editorHost.value })
}

function destroyEditor() {
  view?.destroy()
  view = null
}

// Replaces the editor's whole document in place (undo/redo, and
// refreshing after a save) — `content` updates itself via the
// updateListener already wired in createEditor, so callers never set it
// directly.
function setEditorDoc(newContent) {
  if (!view) return
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: newContent } })
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

async function loadFileContent(fileName) {
  const token = ++requestToken
  loading.value = true
  clearApiError()
  destroyEditor()
  try {
    const file = await getProjectFile(props.projectName, fileName)
    if (token !== requestToken) return // superseded by a newer switch/undo/redo
    content.value = file.content
    originalContent.value = file.content
    canUndo.value = file.can_undo
    canRedo.value = file.can_redo
  } catch {
    if (token === requestToken) loading.value = false
    return
  }
  loading.value = false
  await nextTick() // editorHost is v-show, but wait a tick anyway for layout to settle
  if (token !== requestToken) return
  createEditor(content.value, fileName)
  if (fileName === 'index.yml') applyPendingCursorTarget()
}

// Moves the editor's cursor to a definition clicked in the Inspect panel
// (see jumpToDefinition). Best-effort: a target that findStateLine/
// findActionLine/findSignalLine can't locate (e.g. hand-edited YAML with
// unusual indentation) just leaves the cursor where it was.
function applyPendingCursorTarget() {
  if (!pendingCursorTarget.value || !view) return
  const target = pendingCursorTarget.value
  pendingCursorTarget.value = null
  const lines = content.value.split('\n')
  let lineIndex = null
  if (target.kind === 'state') lineIndex = findStateLine(lines, target.stateKey)
  else if (target.kind === 'action') {
    lineIndex = target.stateKey === '' ? findInitActionLine(lines) : findActionLine(lines, target.stateKey, target.actionName)
  } else if (target.kind === 'signal') lineIndex = findSignalLine(lines, target.signalName)
  if (lineIndex === null) return
  const lineInfo = view.state.doc.line(lineIndex + 1) // CodeMirror lines are 1-based
  view.dispatch({
    selection: { anchor: lineInfo.from, head: lineInfo.from },
    effects: EditorView.scrollIntoView(lineInfo.from, { y: 'center' })
  })
  view.focus()
}

// Entry point for the graph's node/edge taps and the Signals tab's rows.
// index.yml is the only file definitions ever live in — if it isn't the
// one open, routes through the normal (possibly dialog-gated) file switch
// and applies once loadFileContent finishes; already on it, applies right
// away since content/view are already current.
function jumpToDefinition(target) {
  pendingCursorTarget.value = target
  if (currentFileName.value === 'index.yml') {
    applyPendingCursorTarget()
  } else {
    selectFile('index.yml')
  }
}

// Saves whatever file is currently open, in place — purely persistence,
// never navigation: the editor toolbar's own Save button calls this
// directly and stays open regardless of outcome, same as the switch-file
// dialog's "Save" choice. Only Back (see handleClose) ever leaves the
// editor. Returns whether it succeeded; on failure the shared error store
// already has the message.
async function saveCurrentFile() {
  saving.value = true
  clearApiError()
  try {
    const result = await putProjectFile(props.projectName, currentFileName.value, content.value)
    // Refresh from the server's own response (can_undo/can_redo, plus
    // content for consistency) rather than trusting what was typed.
    setEditorDoc(result.content)
    originalContent.value = result.content
    canUndo.value = result.can_undo
    canRedo.value = result.can_redo
    emit('saved')
    // The Inspect panel reflects the last saved state, so a successful
    // save is exactly when it needs to catch up (see toggleInspect).
    if (inspecting.value) await inspectorRef.value?.refresh()
    // A save is the only thing that can change which states exist at all
    // — see isStateGone, which every bubble's own restart-from-here
    // button depends on.
    refreshValidStateKeys()
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

// Undo/redo ask the backend to preview the previous/next content from
// the current user's own history (see api.js's undoProjectFile/
// redoProjectFile), sending the editor's own current content along so a
// later redo/undo can bring it back. Unlike Save, this is a pure editor
// preview: nothing is persisted, and the active project/conversation is
// never reloaded or reconciled (no 'saved' emit, no Inspector/valid-
// state-keys refresh) — only an explicit Save does any of that (see
// saveCurrentFile). `originalContent` deliberately stays put, so the
// editor's content now differing from it is exactly what lights the Save
// button back up. The frontend never navigates by version number, just
// "undo" / "redo".
async function applyHistoryNavigation(action) {
  const token = ++requestToken
  try {
    const file = await action(props.projectName, currentFileName.value, content.value)
    // Superseded by a newer switch/undo/redo (see requestToken's own
    // docstring) — applying it now would overwrite whichever file is
    // actually open with this now-stale one's content.
    if (token !== requestToken) return
    setEditorDoc(file.content)
    canUndo.value = file.can_undo
    canRedo.value = file.can_redo
  } catch {
    // already surfaced via apiFetch
  }
}

function undo() {
  if (canUndo.value) applyHistoryNavigation(undoProjectFile)
}

function redo() {
  if (canRedo.value) applyHistoryNavigation(redoProjectFile)
}

async function switchFile(fileName) {
  currentFileName.value = fileName
  await loadFileContent(fileName)
}

// Entry point for both explorer clicks and post-upload auto-open — routes
// through the unsaved-changes dialog when there's something to lose.
async function selectFile(fileName) {
  if (fileName === currentFileName.value) return
  if (isDirty.value) {
    pendingFileName.value = fileName
    return
  }
  await switchFile(fileName)
}

async function confirmSwitchSave() {
  const target = pendingFileName.value
  pendingFileName.value = null
  if (await saveCurrentFile()) await switchFile(target)
}

async function confirmSwitchDiscard() {
  const target = pendingFileName.value
  pendingFileName.value = null
  await switchFile(target)
}

function confirmSwitchCancel() {
  pendingFileName.value = null
  // A cursor jump that triggered this switch is moot once the switch
  // itself is declined — don't let it fire on some later, unrelated switch.
  pendingCursorTarget.value = null
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
  const rawName = window.prompt('New file name (e.g. notes.txt or extra.yml):')
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
  if (isDirty.value && !window.confirm('Discard unsaved changes to this file?')) return
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
// later re-open via toggleInspect. Inspector.vue loads its own graph/
// signals definitions on mount (see its own onMounted) — this view only
// owns the live/point-in-time pieces layered on top of them.
async function openInspect() {
  await nextTick() // Inspector only exists once the v-if block above mounts
  await Promise.all([refreshNextAction(), refreshSignalValues()])
}

// Toggled by the Inspect button and by the panel's own Close button, same
// as SignalsView's autotracking/close pair. Inspector.vue's own
// onBeforeUnmount handles its cytoscape cleanup when it unmounts (v-if).
function toggleInspect() {
  inspecting.value = !inspecting.value
  if (inspecting.value) openInspect()
}

// The embedded chat has no data of its own to load/unload — it's just a
// second view of chatStore.js's shared conversation (see ChatWindow.vue) —
// so toggling it is nothing more than showing/hiding the panel.
function toggleChat() {
  chatOpen.value = !chatOpen.value
}

// See editorOpen's own docstring for why this is a plain visibility
// flip (v-show) rather than the mount/unmount toggleChat/toggleInspect
// otherwise use (v-if) — nothing here needs loading or tearing down.
function toggleEditor() {
  editorOpen.value = !editorOpen.value
}

function handleDownload() {
  emit('download', props.projectName)
  alert("Project " + props.projectName + " downloaded to your local folder.")
}

function startExplorerDrag(event) {
  dragTarget = 'explorer'
  event.preventDefault()
}

function startInspectorDrag(event) {
  dragTarget = 'inspector'
  event.preventDefault()
}

function startChatDrag(event) {
  dragTarget = 'chat'
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
  } else if (dragTarget === 'chat') {
    // The chat divider sits above the chat panel, so dragging it up
    // (negative movementY) needs to grow the panel, not shrink it.
    chatHeight.value = Math.min(600, Math.max(160, chatHeight.value - event.movementY))
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

watch(saving, (isSaving) => {
  view?.dispatch({ effects: editableCompartment.reconfigure(EditorView.editable.of(!isSaving)) })
})

// A turn can shift signal values enough to change which action would fire
// next even without a state change — see chatStore.js's turnCount. Metrics
// are heavier to compute, so unlike signals they're only refreshed while
// the Inspector's own Metrics tab is the one actually open (see
// Inspector.vue's refreshMetrics) — never prefetched in the background.
// signalsLog, unlike those, feeds the chat timeline itself (transition
// rows, annotation icons) — visible whenever the chat panel is, whether
// or not Inspect is open, so it refreshes unconditionally.
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
  inspectorRef.value?.refreshMetrics()
  inspectorRef.value?.refreshEnv()
})

// Metrics aren't reactive to a prop change on their own (see Inspector.
// vue's refreshMetrics docstring) — a selection change needs its own
// explicit nudge, same as BenchmarkProjectView.vue's own watch(selected).
// Env gets the same nudge: it isn't prop-driven either (it's fetched
// straight from the db, see InspectorEnvTab.vue's own loadEnv), so
// switching which message is selected wouldn't otherwise re-pull it.
watch(selected, () => {
  if (!inspecting.value) return
  nextTick(() => {
    inspectorRef.value?.refreshMetrics()
    inspectorRef.value?.refreshEnv()
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

onMounted(async () => {
  // A fresh editing session starts with a clean undo/redo slate — cleared
  // here (entry), not on Back, and awaited before the first file loads so
  // its own can_undo/can_redo already reflects the cleared state.
  try {
    await clearProjectHistory(props.projectName)
  } catch {
    // already surfaced via apiFetch — the session still opens either way
  }
  loadFiles()
  loadFileContent(currentFileName.value)
  refreshSessionStartState()
  refreshSignalsLog()
  refreshValidStateKeys()
  if (inspecting.value) openInspect()
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', stopDrag)
  window.addEventListener('resize', handleWindowResize)
})
onBeforeUnmount(() => {
  destroyEditor()
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
        <button
          class="chat-toggle-btn"
          :class="{ 'chat-toggle-btn-on': editorOpen }"
          @click="toggleEditor"
        >
          Edit
        </button>
        <button
          class="chat-toggle-btn"
          :class="{ 'chat-toggle-btn-on': chatOpen }"
          @click="toggleChat"
        >
          Chat
        </button>
        <button
          class="inspect-btn"
          :class="{ 'inspect-btn-on': inspecting }"
          @click="toggleInspect"
        >
          Inspect
        </button>
        <button class="inspect-btn" @click="handleDownload">Download</button>
        <button class="reset-btn" @click="handleReset">Reset</button>
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
                <button class="file-explorer-new-btn" :disabled="creatingFile" @click="handleNewFile">
                  {{ creatingFile ? 'Creating…' : '+ New' }}
                </button>
                <button class="file-explorer-upload-btn" :disabled="uploading" @click="triggerUpload">
                  {{ uploading ? 'Uploading…' : '+ Upload' }}
                </button>
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
          </div>

          <div class="split-divider" @mousedown="startExplorerDrag"></div>

          <div class="edit-project-editor-pane">
            <div class="edit-project-editor-toolbar">
              <span class="edit-project-editor-filename">{{ currentFileName }}</span>
              <div class="edit-project-editor-toolbar-actions">
                <button
                  class="undo-redo-btn"
                  title="Undo"
                  :disabled="loading || saving || !canUndo"
                  @click="undo"
                >↶</button>
                <button
                  class="undo-redo-btn"
                  title="Redo"
                  :disabled="loading || saving || !canRedo"
                  @click="redo"
                >↷</button>
                <button class="save-btn" :disabled="loading || saving || !isDirty" @click="saveCurrentFile">
                  {{ saving ? 'Saving…' : 'Save' }}
                </button>
              </div>
            </div>
            <div class="edit-project-editor-content">
              <p v-if="loading" class="edit-project-status">Loading…</p>
              <div v-show="!loading" ref="editorHost" class="edit-project-editor"></div>
            </div>
          </div>
        </div>

        <Transition name="panel-slide-bottom">
        <div v-if="chatOpen" class="edit-project-chat-wrap" :class="{ 'edit-project-chat-wrap-full': !editorOpen }">
          <!-- Nothing to split against once the editor row is hidden (see
               editorOpen) — the divider drags the boundary between it and
               chat, which no longer exists, and the chat panel below
               switches to filling the whole column instead of its own
               fixed, drag-adjusted chatHeight. -->
          <div v-if="editorOpen" class="split-divider-horizontal" @mousedown="startChatDrag"></div>

          <div class="edit-project-chat-panel" :style="editorOpen ? { height: chatHeight + 'px' } : null">
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
                <button
                  class="sessions-btn"
                  :class="{ 'sessions-btn-active': sessionsPanelOpen }"
                  title="Sessions"
                  @click="toggleSessionsPanel"
                >
                  Sessions
                </button>
                <ModelMenu />
              </div>
            </div>
            <ChatWindow>
              <template #timeline>
                <ChatTimeline
                  :timeline="timeline"
                  :signals-log="signalsLog"
                  :selected="selected"
                  :spoken-text-enabled="spokenTextEnabled"
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

      <Transition name="panel-slide-right">
      <div v-if="inspecting" class="inspector-wrap">
        <div class="split-divider inspector-divider" @mousedown="startInspectorDrag"></div>

        <div class="inspector-panel" :style="{ '--inspector-width': inspectorWidth + 'px' }">
          <Inspector
            ref="inspectorRef"
            :project-name="projectName"
            :highlighted-state-key="highlightedStateKey"
            :auto-jump-on-highlight-change="true"
            :next-action-edge="selected ? null : nextAction"
            :fired-action-edge="firedActionEdge"
            :signal-values="effectiveSignalValues"
            :until-message-id="untilMessageId"
            :env-editable="envEditable"
            :editable-files="files"
            @jump-to-definition="jumpToDefinition"
            @select-attachment="selectFile"
            @close="toggleInspect"
          />
        </div>
      </div>
      </Transition>
    </div>

    <div v-if="pendingFileName" class="switch-dialog-overlay">
      <div class="switch-dialog">
        <p>"{{ currentFileName }}" has unsaved changes. Save before switching to "{{ pendingFileName }}"?</p>
        <div class="switch-dialog-actions">
          <button class="switch-dialog-save-btn" :disabled="saving" @click="confirmSwitchSave">Save</button>
          <button class="switch-dialog-discard-btn" :disabled="saving" @click="confirmSwitchDiscard">Discard</button>
          <button class="switch-dialog-cancel-btn" :disabled="saving" @click="confirmSwitchCancel">Cancel</button>
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

.chat-toggle-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.chat-toggle-btn:hover {
  background: #eef2f9;
}

.chat-toggle-btn-on {
  background: #4a6fa5;
  color: white;
}

.chat-toggle-btn-on:hover {
  background: #3d5c8a;
}

.inspect-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.inspect-btn:hover {
  background: #eef2f9;
}

.inspect-btn-on {
  background: #4a6fa5;
  color: white;
}

.inspect-btn-on:hover {
  background: #3d5c8a;
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

.split-divider-horizontal {
  flex-shrink: 0;
  height: 6px;
  margin: 0.4rem 0;
  border-radius: 3px;
  background: transparent;
  cursor: row-resize;
}

.split-divider-horizontal:hover {
  background: #dbe4f0;
}

.edit-project-chat-wrap {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

/* editorOpen is false — see the v-if/v-else... above: no fixed
   chatHeight to honor anymore, so this (and its own chat-panel) grow to
   fill whatever space the now-hidden top-row would have used instead. */
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

.edit-project-chat-toolbar-actions .sessions-btn {
  padding: 0.35rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 0.85rem;
  cursor: pointer;
}

.edit-project-chat-toolbar-actions .sessions-btn:hover {
  background: #4a6fa5;
  color: white;
}

.edit-project-chat-toolbar-actions .sessions-btn-active {
  background: #4a6fa5;
  color: white;
}

.edit-project-header-actions .reset-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
}

.edit-project-header-actions .reset-btn:hover {
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

.file-explorer-new-btn {
  padding: 0.25rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.78rem;
}

.file-explorer-new-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.file-explorer-new-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.file-explorer-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.file-explorer-upload-btn {
  padding: 0.25rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.78rem;
}

.file-explorer-upload-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.file-explorer-upload-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
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

.panel-slide-right-enter-active,
.panel-slide-right-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.panel-slide-right-enter-from,
.panel-slide-right-leave-to {
  opacity: 0;
  transform: translateX(16px);
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
  }
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
