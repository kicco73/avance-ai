<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import cytoscape from 'cytoscape'
import ChatWindow from './ChatWindow.vue'
import {
  getProjectFiles,
  getProjectFile,
  putProjectFile,
  deleteProjectFile,
  getProjectSignals,
  getProjectGraph,
  getSignals,
  postTriggersPreview
} from '../api.js'
import { clearApiError, errorDetail, errorMessage, setApiError } from '../errorStore.js'
import { hasSignalValue, useSignalChangeFlash } from '../signalDisplay.js'
// Aliased: this file already uses "state" to mean an automaton state node
// (see the graph/signals data below) — `liveState` is specifically the
// live conversation's current state, the single source of truth this
// view's Inspector syncs itself to (see syncSelectionToCurrentState).
import {
  state as liveState,
  turnCount,
  autoTrackingEnabled,
  autoTrackingLoading,
  toggleAutoTracking,
  handleReset
} from '../chatStore.js'

const props = defineProps({
  projectName: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['close', 'saved'])

const UPLOADABLE_PATTERN = /\.(txt|ya?ml)$/i
const YAML_PATTERN = /\.ya?ml$/i

// [a] [b] [c] ... labels for the Inspect panel's attachment buttons —
// position within a single state/signal's own attachments list, not a
// project-wide index.
function attachmentLabel(index) {
  return String.fromCharCode(97 + index)
}

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
const showErrorDetail = ref(false)
const editorHost = ref(null)
const uploadInput = ref(null)

// Inspect panel: shows the last-saved project's state machine graph and
// signal definitions as two tabs (each gets the panel's full space), see
// toggleInspect/setInspectorTab. Open by default.
const inspecting = ref(true)
const inspectorTab = ref('graph') // 'graph' | 'signals'
const signalsLoading = ref(true)
const signals = ref([])
// Live value/error per signal name, keyed separately from `signals` (the
// project's saved definitions) since they come from a different source
// (getSignals(), the same live conversation ChatWindow reads) and refresh
// on a different cadence — see refreshSignalValues.
const signalValueByName = ref({})
const { recentlyChanged: recentlyChangedSignals, markChanged: markSignalsChanged } = useSignalChangeFlash()
const graphLoading = ref(true)
const graphHost = ref(null)
const inspectorWidth = ref(360)

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

// The editor's own doc is the source of truth for content while it's
// mounted — this ref only mirrors it (via the updateListener below) so
// save() has something to send without querying the view directly.
const content = ref('')
// What was last loaded/saved for the current file — compared against
// `content` to decide whether switching/closing needs a confirmation.
const originalContent = ref('')
const isDirty = computed(() => content.value !== originalContent.value)

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

function createEditor(doc, fileName) {
  const extensions = [
    basicSetup,
    EditorView.lineWrapping,
    editableCompartment.of(EditorView.editable.of(true)),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) content.value = update.state.doc.toString()
    })
  ]
  if (YAML_PATTERN.test(fileName)) extensions.splice(1, 0, yaml())
  view = new EditorView({ doc, extensions, parent: editorHost.value })
}

function destroyEditor() {
  view?.destroy()
  view = null
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
  loading.value = true
  clearApiError()
  destroyEditor()
  try {
    content.value = await getProjectFile(props.projectName, fileName)
    originalContent.value = content.value
  } catch {
    loading.value = false
    return
  }
  loading.value = false
  await nextTick() // editorHost is v-show, but wait a tick anyway for layout to settle
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
  else if (target.kind === 'action') lineIndex = findActionLine(lines, target.stateKey, target.actionName)
  else if (target.kind === 'signal') lineIndex = findSignalLine(lines, target.signalName)
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
    await putProjectFile(props.projectName, currentFileName.value, content.value)
    originalContent.value = content.value
    emit('saved')
    // The Inspect panel reflects the last saved state, so a successful
    // save is exactly when it needs to catch up (see toggleInspect).
    if (inspecting.value) await Promise.all([loadSignals(), loadGraph()])
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
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
// (nothing typed, or already saved) closes straight away.
function handleClose() {
  if (isDirty.value && !window.confirm('Discard unsaved changes to this file?')) return
  emit('close')
}

async function loadSignals() {
  signalsLoading.value = true
  try {
    signals.value = (await getProjectSignals(props.projectName)).signals
  } catch {
    // already surfaced via apiFetch
  } finally {
    signalsLoading.value = false
  }
}

// Live values for whatever signals the active conversation currently has
// (see chatStore.js's shared state — the same getSignals() ChatWindow's
// own Signals concerns already rely on via SignalsView). Kept separate
// from loadSignals()'s definitions so either can refresh on its own
// cadence — definitions rarely change; values do on every turn (see the
// turnCount watcher below).
async function refreshSignalValues() {
  try {
    const nextValues = await getSignals()
    const previousValues = Object.entries(signalValueByName.value).map(([name, v]) => ({ name, ...v }))
    markSignalsChanged(previousValues, nextValues)
    signalValueByName.value = Object.fromEntries(nextValues.map((s) => [s.name, { value: s.value, error: s.error }]))
  } catch {
    // already surfaced via apiFetch
  }
}

let cyGraph = null

// The state or action last tapped in the graph — { kind: 'state'|'action',
// data } or null. Non-null shrinks the graph box to make room for its
// detail card below (see .inspector-graph-section's flex layout).
const selectedElement = ref(null)

// Raw graph data (as fetched, not the cytoscape-shaped elements) — kept
// around so syncSelectionToCurrentState can look a live state key up by
// key without re-fetching, the same way a click already has its node's
// data on hand via evt.target.data().
const graphNodes = ref([])
const graphEdges = ref([])

// { stateKey, actionName } of the action the engine would fire next from
// the live current state, or null — see refreshNextAction. Reuses
// postTriggersPreview (already computed for SignalsView's own "Next
// triggerable action" section) instead of re-deriving trigger evaluation
// here.
const nextAction = ref(null)

// Whether the currently selected detail card is showing exactly that
// action — drives the green "Next" badge (see the action detail template).
const isSelectedActionNext = computed(() => {
  if (selectedElement.value?.kind !== 'action' || !nextAction.value) return false
  return (
    selectedElement.value.data.source === nextAction.value.stateKey &&
    selectedElement.value.data.actionName === nextAction.value.actionName
  )
})

// Whether the currently selected detail card is showing the live
// conversation's current state — drives the "Current" badge, in the same
// amber used for the graph's own current-state highlight (see
// .node.current-state in renderGraph and .inspector-detail-badge-current).
const isSelectedStateCurrent = computed(() => {
  return selectedElement.value?.kind === 'state' && selectedElement.value.data.id === liveState.value?.key
})

// Whether the detail card has any non-type badge to show at all — an
// action that's neither Next nor Manual (a plain triggered action) has
// none, and the badges row should collapse rather than render empty.
const hasSelectedElementBadges = computed(() => {
  if (!selectedElement.value) return false
  if (selectedElement.value.kind === 'state') {
    const d = selectedElement.value.data
    return isSelectedStateCurrent.value || d.isStart || d.final || !d.chat || d.historyCutoff
  }
  return isSelectedActionNext.value || !selectedElement.value.data.hasTrigger
})

function destroyGraph() {
  cyGraph?.destroy()
  cyGraph = null
}

// The cytoscape-data shape for a state node — camelCase, one place this
// mapping is decided so a tap handler (which reads it straight off
// evt.target.data()) and the current-state auto-sync (which builds it
// from the raw fetched node instead) never drift apart.
function nodeToCyData(n) {
  return {
    id: n.key,
    uiLabel: n.ui_label,
    uiDescription: n.ui_description,
    final: n.final,
    isStart: n.is_start,
    chat: n.chat,
    onEnter: n.on_enter,
    historyCutoff: n.history_cutoff,
    transitionLogLevel: n.transition_log_level,
    attachments: n.attachments
  }
}

// Same idea as nodeToCyData, for an action edge.
function edgeToCyData(e, id) {
  return {
    id,
    source: e.source,
    target: e.target,
    uiLabel: e.ui_label,
    uiDescription: e.ui_description,
    actionName: e.action_name,
    buttonText: e.ui_button,
    trigger: e.trigger,
    hasTrigger: e.has_trigger,
    actionPrompt: e.action_prompt
  }
}

function graphElements(nodes, edges) {
  return [
    ...nodes.map((n) => ({ data: nodeToCyData(n) })),
    ...edges.map((e, i) => ({ data: edgeToCyData(e, `edge-${i}`) }))
  ]
}

// Selecting/deselecting resizes the graph box (see .inspector-graph-section),
// so Cytoscape needs a nudge once the layout settles.
function selectGraphElement(kind, data) {
  selectedElement.value = { kind, data }
  nextTick(() => cyGraph?.resize())
}

function closeGraphDetail() {
  selectedElement.value = null
  nextTick(() => cyGraph?.resize())
}

// A tap on a node/edge both opens its detail card and jumps the editor's
// cursor to its definition — see selectGraphElement/jumpToDefinition.
function handleNodeTap(evt) {
  const data = evt.target.data()
  selectGraphElement('state', data)
  jumpToDefinition({ kind: 'state', stateKey: data.id })
}

function handleEdgeTap(evt) {
  const data = evt.target.data()
  selectGraphElement('action', data)
  jumpToDefinition({ kind: 'action', stateKey: data.source, actionName: data.actionName })
}

// Renders the state machine into graphHost with Cytoscape — a fresh
// instance every time (cheap for graphs this size), rooted breadthfirst on
// the start state so the flow reads top-to-bottom/left-to-right like a
// diagram instead of a force-directed tangle.
function renderGraph(nodes, edges) {
  destroyGraph()
  if (!graphHost.value) return
  const startKey = nodes.find((n) => n.is_start)?.key
  cyGraph = cytoscape({
    container: graphHost.value,
    elements: graphElements(nodes, edges),
    style: [
      {
        selector: 'node',
        style: {
          'background-color': '#eef2f9',
          'border-width': 2,
          'border-color': '#4a6fa5',
          label: 'data(uiLabel)',
          'text-valign': 'center',
          'text-halign': 'center',
          'font-size': '9px',
          color: '#333',
          shape: 'round-rectangle',
          width: 'label',
          height: 'label',
          padding: '8px',
          'text-wrap': 'wrap',
          'text-max-width': '80px'
        }
      },
      {
        selector: 'node[?final]',
        style: {
          'border-width': 4,
          'border-color': '#c62828',
          'background-color': '#fdecea'
        }
      },
      {
        selector: 'node[?isStart]',
        style: {
          'border-color': '#2e7d32',
          'background-color': '#eaf6ea'
        }
      },
      {
        // The live conversation's current state — an overlay glow rather
        // than a border/background change, so it composes cleanly with
        // final/start's own colors instead of fighting them for the same
        // visual channel (see syncSelectionToCurrentState/
        // applyCurrentStateHighlight).
        selector: 'node.current-state',
        style: {
          'overlay-color': '#f5a623',
          'overlay-opacity': 0.35,
          'overlay-padding': 6
        }
      },
      {
        selector: 'edge',
        style: {
          width: 1.5,
          'line-color': '#9ab0cc',
          'target-arrow-color': '#9ab0cc',
          'target-arrow-shape': 'triangle',
          'arrow-scale': 0.8,
          'curve-style': 'bezier',
          label: 'data(uiLabel)',
          'font-size': '7px',
          color: '#666',
          'text-background-color': 'white',
          'text-background-opacity': 0.85,
          'text-background-padding': '2px',
          'text-wrap': 'wrap',
          'text-max-width': '70px'
        }
      },
      {
        // No trigger = manual-only action (see AutomatonBuilder) — dashed
        // to set it apart from the default solid line, which therefore
        // reads as "automatic" (has a trigger) without needing its own
        // rule. Never inferred from YAML text — `hasTrigger` comes
        // straight from the backend's graph endpoint.
        selector: 'edge[!hasTrigger]',
        style: {
          'line-style': 'dashed'
        }
      },
      {
        // The action postTriggersPreview says would fire next from the
        // live current state — see refreshNextAction/
        // applyNextActionHighlight. Always a triggered (solid) edge, since
        // only triggered actions are ever candidates.
        selector: 'edge.next-action',
        style: {
          'line-color': '#2e7d32',
          'target-arrow-color': '#2e7d32',
          width: 2.5
        }
      },
      {
        selector: 'edge[source = target]',
        style: {
          'curve-style': 'loop',
          'loop-direction': '-45deg',
          'loop-sweep': '45deg'
        }
      }
    ],
    layout: {
      name: 'breadthfirst',
      directed: true,
      roots: startKey ? [startKey] : undefined,
      padding: 16,
      spacingFactor: 1.1
    }
  })
  cyGraph.on('tap', 'node', handleNodeTap)
  cyGraph.on('tap', 'edge', handleEdgeTap)
  cyGraph.on('tap', (evt) => {
    if (evt.target === cyGraph) closeGraphDetail()
  })

  // A (re)build can rename/remove whatever was selected before, and is
  // also the moment a freshly opened Inspector needs to catch up with
  // reality — re-sync to the live current state either way. No cursor
  // jump here: unlike an actual state change (see the liveState watcher
  // below), a reload triggered by an unrelated file save must not yank
  // focus away from whatever the user is doing.
  applyCurrentStateHighlight()
  applyNextActionHighlight()
  syncSelectionToCurrentState()
}

async function loadGraph() {
  graphLoading.value = true
  try {
    const { nodes, edges } = await getProjectGraph(props.projectName)
    graphNodes.value = nodes
    graphEdges.value = edges
    renderGraph(nodes, edges)
  } catch {
    // already surfaced via apiFetch
  } finally {
    graphLoading.value = false
  }
}

// Recolors the live current state's node — see the node.current-state
// style rule in renderGraph. A no-op while the graph isn't built
// (Inspector closed) or the live state doesn't belong to this project's
// graph (e.g. editing a project other than the currently active one).
function applyCurrentStateHighlight() {
  if (!cyGraph) return
  cyGraph.nodes().removeClass('current-state')
  const key = liveState.value?.key
  if (key == null) return
  cyGraph.nodes().filter((n) => n.id() === key).addClass('current-state')
}

// Recolors the edge postTriggersPreview says would fire next — see the
// edge.next-action style rule in renderGraph.
function applyNextActionHighlight() {
  if (!cyGraph) return
  cyGraph.edges().removeClass('next-action')
  if (!nextAction.value) return
  cyGraph
    .edges()
    .filter((edge) => edge.data('source') === nextAction.value.stateKey && edge.data('actionName') === nextAction.value.actionName)
    .addClass('next-action')
}

// Mirrors what tapping the live current state's node in the graph would
// do — same selection (and, when `jumpCursor` is true, the same editor
// cursor jump) — so the Inspector automatically tracks whatever the
// actual conversation is doing (autotracking, manual actions, reset, ...),
// not just clicks made inside the graph itself. `jumpCursor` is only true
// for an actual state-change event (see the liveState watcher below); a
// routine graph reload (renderGraph) re-syncs the selection without it.
function syncSelectionToCurrentState({ jumpCursor = false } = {}) {
  const key = liveState.value?.key
  const node = key == null ? null : graphNodes.value.find((n) => n.key === key)
  if (!node) {
    selectedElement.value = null
    return
  }
  selectGraphElement('state', nodeToCyData(node))
  if (jumpCursor) jumpToDefinition({ kind: 'state', stateKey: node.key })
}

// Reuses the same triggers-preview endpoint SignalsView already calls for
// its own "Next triggerable action" section — no separate client-side
// reimplementation of trigger evaluation. would_fire's own FIFO-priority
// logic (see backend Automaton.preview_triggers) decides the winner; this
// just finds it.
async function refreshNextAction() {
  const stateKeyAtFetch = liveState.value?.key
  if (stateKeyAtFetch == null) {
    nextAction.value = null
    applyNextActionHighlight()
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
  applyNextActionHighlight()
}

// Switching to the graph tab can make graphHost visible again after being
// hidden (v-show) while the panel had its cytoscape instance already
// built — a resize/fit is enough to make it render correctly since the
// breadthfirst layout's node positions never depended on container size.
function setInspectorTab(tab) {
  inspectorTab.value = tab
  if (tab === 'graph') {
    nextTick(() => {
      cyGraph?.resize()
      cyGraph?.fit()
    })
  }
}

// Shared by the initial mount (Inspect is open by default) and every
// later re-open via toggleInspect.
async function openInspect() {
  await nextTick() // graphHost only exists once the v-if block above mounts
  await Promise.all([loadSignals(), loadGraph(), refreshNextAction(), refreshSignalValues()])
}

// Toggled by the Inspect button and by the panel's own Close button, same
// as SignalsView's autotracking/close pair.
async function toggleInspect() {
  inspecting.value = !inspecting.value
  if (inspecting.value) await openInspect()
  else destroyGraph()
}

// The embedded chat has no data of its own to load/unload — it's just a
// second view of chatStore.js's shared conversation (see ChatWindow.vue) —
// so toggling it is nothing more than showing/hiding the panel.
function toggleChat() {
  chatOpen.value = !chatOpen.value
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
    cyGraph?.resize()
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
  cyGraph?.resize()
}

watch(saving, (isSaving) => {
  view?.dispatch({ effects: editableCompartment.reconfigure(EditorView.editable.of(!isSaving)) })
})

// The live conversation's current state changing — autotracking, a manual
// action, a reset, anything — is treated exactly like the user clicking
// that state in the graph: same selection, same cursor jump. Gated on the
// Inspector being open since there's no graph to highlight/click-equivalent
// otherwise, and jumping the editor's cursor while the user isn't even
// looking at the Inspector would be pure disruption.
watch(
  () => liveState.value?.key,
  () => {
    if (!inspecting.value) return
    applyCurrentStateHighlight()
    syncSelectionToCurrentState({ jumpCursor: true })
  }
)

// A turn can shift signal values enough to change which action would fire
// next even without a state change — see chatStore.js's turnCount. The
// Signals tab's bars need the same refresh to stay live regardless of
// which tab is actually visible right now (v-show, not v-if — see
// setInspectorTab).
watch(turnCount, () => {
  if (!inspecting.value) return
  refreshNextAction()
  refreshSignalValues()
})

onMounted(() => {
  loadFiles()
  loadFileContent(currentFileName.value)
  if (inspecting.value) openInspect()
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', stopDrag)
  window.addEventListener('resize', handleWindowResize)
})
onBeforeUnmount(() => {
  destroyEditor()
  destroyGraph()
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
        <button class="close-btn" @click="handleClose">Back</button>
      </div>
    </div>

    <div v-if="errorMessage" class="edit-project-error-row">
      <p class="edit-project-error">{{ errorMessage }}</p>
      <button
        v-if="errorDetail"
        type="button"
        class="edit-project-error-details-btn"
        @click="showErrorDetail = !showErrorDetail"
      >
        {{ showErrorDetail ? 'Hide details' : 'Details' }}
      </button>
    </div>
    <pre v-if="errorMessage && errorDetail && showErrorDetail" class="edit-project-error-detail">{{ errorDetail }}</pre>

    <div class="edit-project-body">
      <div class="edit-project-main-column">
        <div class="edit-project-top-row">
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
              <button class="save-btn" :disabled="loading || saving" @click="saveCurrentFile">
                {{ saving ? 'Saving…' : 'Save' }}
              </button>
            </div>
            <div class="edit-project-editor-content">
              <p v-if="loading" class="edit-project-status">Loading…</p>
              <div v-show="!loading" ref="editorHost" class="edit-project-editor"></div>
            </div>
          </div>
        </div>

        <template v-if="chatOpen">
          <div class="split-divider-horizontal" @mousedown="startChatDrag"></div>

          <div class="edit-project-chat-panel" :style="{ height: chatHeight + 'px' }">
            <div class="edit-project-chat-toolbar">
              <button class="reset-btn" @click="handleReset">Reset</button>
              <button class="close-x-btn" title="Close" @click="toggleChat">×</button>
            </div>
            <ChatWindow />
          </div>
        </template>
      </div>

      <template v-if="inspecting">
        <div class="split-divider inspector-divider" @mousedown="startInspectorDrag"></div>

        <div class="inspector-panel" :style="{ '--inspector-width': inspectorWidth + 'px' }">
          <div class="inspector-header">
            <button
              class="autotracking-btn"
              :class="{ 'autotracking-btn-on': autoTrackingEnabled }"
              :disabled="autoTrackingLoading"
              @click="toggleAutoTracking"
            >
              Autotracking
            </button>
            <button class="close-x-btn" title="Close" @click="toggleInspect">×</button>
          </div>
          <div class="inspector-tabs">
            <button
              class="inspector-tab-btn"
              :class="{ 'inspector-tab-btn-active': inspectorTab === 'graph' }"
              @click="setInspectorTab('graph')"
            >
              State machine
            </button>
            <button
              class="inspector-tab-btn"
              :class="{ 'inspector-tab-btn-active': inspectorTab === 'signals' }"
              @click="setInspectorTab('signals')"
            >
              Signals
            </button>
          </div>

          <div class="inspector-body">
            <div v-show="inspectorTab === 'graph'" class="inspector-graph-section">
              <div class="inspector-graph-host-wrap">
                <p v-if="graphLoading" class="signals-status inspector-graph-status">Loading…</p>
                <div ref="graphHost" class="inspector-graph-host"></div>
              </div>

              <div v-if="selectedElement" class="inspector-detail-card">
                <div class="inspector-detail-header">
                  <div class="inspector-detail-header-top">
                    <!-- Type badge sits right next to the title, same as
                         Signal's own badge+name pairing (.inspector-signal-header)
                         — only the type tag lives here, everything else
                         (Current, Start, Final, Next, Manual, ...) is below. -->
                    <span
                      class="inspector-detail-badge"
                      :class="selectedElement.kind === 'state' ? 'inspector-detail-badge-state' : 'inspector-detail-badge-action'"
                    >
                      {{ selectedElement.kind === 'state' ? 'State' : 'Action' }}
                    </span>
                    <span class="inspector-detail-title">{{ selectedElement.data.uiLabel }}</span>
                    <button class="close-x-btn" title="Close" @click="closeGraphDetail">×</button>
                  </div>

                  <!-- One badge language for every other tag a state/action
                       can carry (Current, Start, Final, Next, Manual, ...) —
                       see .inspector-detail-badge and its color variants below. -->
                  <div v-if="hasSelectedElementBadges" class="inspector-detail-badges">
                    <template v-if="selectedElement.kind === 'state'">
                      <span v-if="isSelectedStateCurrent" class="inspector-detail-badge inspector-detail-badge-current">
                        Current
                      </span>
                      <span v-if="selectedElement.data.isStart" class="inspector-detail-badge inspector-detail-badge-start">
                        Start
                      </span>
                      <span v-if="selectedElement.data.final" class="inspector-detail-badge inspector-detail-badge-final">
                        Final
                      </span>
                      <span v-if="!selectedElement.data.chat" class="inspector-detail-badge inspector-detail-badge-neutral">
                        No chat
                      </span>
                      <span v-if="selectedElement.data.historyCutoff" class="inspector-detail-badge inspector-detail-badge-neutral">
                        History cutoff
                      </span>
                    </template>
                    <template v-else>
                      <span v-if="isSelectedActionNext" class="inspector-detail-badge inspector-detail-badge-next">
                        Next
                      </span>
                      <span v-if="!selectedElement.data.hasTrigger" class="inspector-detail-badge inspector-detail-badge-manual">
                        Manual
                      </span>
                    </template>
                  </div>
                </div>

                <div class="inspector-detail-body">
                  <template v-if="selectedElement.kind === 'state'">
                    <p v-if="selectedElement.data.uiDescription" class="inspector-detail-ui_description">
                      {{ selectedElement.data.uiDescription }}
                    </p>
                    <p v-if="selectedElement.data.onEnter" class="inspector-detail-field">
                      <strong>On enter:</strong> {{ selectedElement.data.onEnter }}
                    </p>
                  </template>
                  <template v-else>
                    <p v-if="selectedElement.data.uiDescription" class="inspector-detail-ui_description">
                      {{ selectedElement.data.uiDescription }}
                    </p>
                    <p class="inspector-detail-field">
                      <strong>{{ selectedElement.data.source }}</strong> → <strong>{{ selectedElement.data.target }}</strong>
                    </p>
                    <p v-if="selectedElement.data.buttonText" class="inspector-detail-field">
                      <strong>Button:</strong> {{ selectedElement.data.buttonText }}
                    </p>
                    <p v-if="selectedElement.data.trigger" class="inspector-detail-field">
                      <strong>Trigger:</strong>
                      <code class="inspector-detail-code">{{ selectedElement.data.trigger }}</code>
                    </p>
                    <p v-if="selectedElement.data.actionPrompt" class="inspector-detail-field">
                      <strong>Action prompt:</strong> {{ selectedElement.data.actionPrompt }}
                    </p>
                  </template>

                  <!-- Actions never carry attachments (only states/signals
                       do — see automaton.py's Action dataclass), so this is
                       naturally absent there; no separate branch needed. -->
                  <div v-if="selectedElement.data.attachments?.length" class="inspector-attachments">
                    <button
                      v-for="(fileName, idx) in selectedElement.data.attachments"
                      :key="fileName"
                      class="inspector-attachment-btn"
                      :class="{ 'inspector-attachment-btn-disabled': !files.includes(fileName) }"
                      :disabled="!files.includes(fileName)"
                      :title="files.includes(fileName) ? fileName : `${fileName} (not text-editable)`"
                      @click.stop="selectFile(fileName)"
                    >
                      {{ attachmentLabel(idx) }}
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <div v-show="inspectorTab === 'signals'" class="inspector-signals-section">
              <p v-if="signalsLoading" class="signals-status">Loading…</p>
              <p v-else-if="!signals.length" class="signals-status">No signals defined.</p>
              <div v-else class="inspector-signal-list">
                <div
                  v-for="signal in signals"
                  :key="signal.name"
                  class="inspector-signal-block inspector-signal-block-clickable"
                  title="Jump to definition"
                  @click="jumpToDefinition({ kind: 'signal', signalName: signal.name })"
                >
                  <div class="inspector-signal-header">
                    <span class="inspector-detail-badge inspector-detail-badge-signal">Signal</span>
                    <span class="inspector-signal-name">{{ signal.ui_label || signal.name }}</span>
                  </div>
                  <span v-if="signal.ui_description" class="inspector-signal-ui_description">
                    {{ signal.ui_description }}
                  </span>

                  <div v-if="signal.attachments?.length" class="inspector-attachments">
                    <button
                      v-for="(fileName, idx) in signal.attachments"
                      :key="fileName"
                      class="inspector-attachment-btn"
                      :class="{ 'inspector-attachment-btn-disabled': !files.includes(fileName) }"
                      :disabled="!files.includes(fileName)"
                      :title="files.includes(fileName) ? fileName : `${fileName} (not text-editable)`"
                      @click.stop="selectFile(fileName)"
                    >
                      {{ attachmentLabel(idx) }}
                    </button>
                  </div>

                  <div class="inspector-signal-bar-track">
                    <div
                      v-if="hasSignalValue(signalValueByName[signal.name])"
                      class="inspector-signal-bar-fill"
                      :class="{ 'inspector-signal-bar-changed': recentlyChangedSignals.has(signal.name) }"
                      :style="{ width: signalValueByName[signal.name].value + '%' }"
                    ></div>
                    <div
                      v-else
                      class="inspector-signal-bar-fill inspector-signal-bar-na"
                      :class="{ 'inspector-signal-bar-changed': recentlyChangedSignals.has(signal.name) }"
                    ></div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
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

.edit-project-error-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
  background: #fdecea;
  border-bottom: 1px solid #f5c6c2;
}

.edit-project-error {
  margin: 0;
  color: #c62828;
  font-size: 0.9rem;
  flex: 1;
}

.edit-project-error-details-btn {
  padding: 0.2rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
  font-size: 0.8rem;
}

.edit-project-error-detail {
  margin: 0;
  padding: 0.75rem 1rem;
  background: #fdecea;
  border-bottom: 1px solid #f5c6c2;
  color: #7a1f1f;
  font-size: 0.8rem;
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
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
  justify-content: flex-end;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #f5f5f7;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

.edit-project-chat-toolbar .reset-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
}

.edit-project-chat-toolbar .reset-btn:hover {
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

.inspector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #f5f5f7;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

.inspector-tabs {
  display: flex;
  gap: 0.25rem;
  padding: 0.5rem 1rem 0;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

.inspector-tab-btn {
  padding: 0.45rem 0.9rem;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: none;
  cursor: pointer;
  font-size: 0.82rem;
  color: #666;
}

.inspector-tab-btn:hover {
  color: #333;
}

.inspector-tab-btn-active {
  color: #2c4d7a;
  font-weight: 600;
  border-bottom-color: #4a6fa5;
}

.inspector-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 1rem;
}

.signals-status {
  margin: 0;
  color: #444;
  font-size: 0.9rem;
}

.inspector-graph-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.inspector-graph-host-wrap {
  position: relative;
  flex: 1;
  min-height: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
  background: #fcfcfd;
  overflow: hidden;
}

.inspector-graph-host {
  width: 100%;
  height: 100%;
}

.inspector-graph-status {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.inspector-detail-card {
  flex-shrink: 0;
  margin-top: 0.75rem;
  max-height: 45%;
  display: flex;
  flex-direction: column;
  border-radius: 8px;
  border: 1px solid #eee;
  background: #fafafa;
  overflow: hidden;
}

.inspector-detail-header {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid #eee;
  flex-shrink: 0;
}

.inspector-detail-header-top {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

/* Every tag a state/action can carry (type, Current, Start, Final, Next,
   Manual, ...) lives in this one row, using the one badge component below
   — same structure/alignment/position regardless of which tag it is. */
.inspector-detail-badges {
  display: flex;
  flex-wrap: wrap;
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

.inspector-detail-badge-state {
  background: #4a6fa5;
}

.inspector-detail-badge-action {
  background: #8a6d3b;
}

.inspector-detail-badge-signal {
  background: #6a4c93;
}

.inspector-detail-badge-current {
  /* Same amber as node.current-state's overlay in renderGraph — this is
     the one other place "this is the live current state" is shown. */
  background: #f5a623;
  color: #3a2600;
}

.inspector-detail-badge-start,
.inspector-detail-badge-next {
  /* Both read as "green = this is where the flow is/begins" — never on
     the same card (Start is state-only, Next is action-only), so sharing
     the hue reinforces one language instead of splitting it. */
  background: #2e7d32;
}

.inspector-detail-badge-final {
  background: #c62828;
}

.inspector-detail-badge-manual {
  background: #5c6b7a;
}

.inspector-detail-badge-neutral {
  /* Minor informational flags (No chat, History cutoff) — same component,
     deliberately unsaturated so they read as secondary to the semantic
     (colored) badges. */
  background: #8a8a8a;
}

.inspector-detail-title {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The one "×" close-panel button style, reused everywhere a panel needs
   to close itself: the detail card (below), the Inspector's own header,
   and the embedded chat panel's toolbar — one visual language instead of
   a different button per panel. */
.close-x-btn {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #666;
  cursor: pointer;
  font-size: 1rem;
}

.close-x-btn:hover {
  background: #eee;
}

.inspector-detail-body {
  padding: 0.6rem 0.75rem;
  overflow-y: auto;
  font-size: 0.8rem;
  color: #444;
}

.inspector-detail-ui_description {
  margin: 0 0 0.5rem;
  line-height: 1.4;
}

.inspector-detail-field {
  margin: 0 0 0.4rem;
  line-height: 1.4;
}

.inspector-detail-code {
  font-size: 0.75rem;
  background: #eee;
  border-radius: 4px;
  padding: 0.1rem 0.4rem;
}

/* Always the last thing in a box (state/action detail body, signal
   block), left-aligned — see attachmentLabel/selectFile. */
.inspector-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-top: 0.5rem;
}

.inspector-attachment-btn {
  width: 1.5rem;
  height: 1.5rem;
  line-height: 1;
  border-radius: 4px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.72rem;
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.inspector-attachment-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.inspector-attachment-btn-disabled {
  border-color: #ccc;
  color: #aaa;
  cursor: not-allowed;
}

.inspector-attachment-btn-disabled:hover {
  background: white;
  color: #aaa;
}

.inspector-signals-section {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.inspector-signal-list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

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

.inspector-signal-name {
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
}

.inspector-signal-ui_description {
  font-size: 0.78rem;
  color: #666;
  line-height: 1.4;
}

.inspector-signal-bar-track {
  margin-top: 0.4rem;
  height: 10px;
  border-radius: 999px;
  background: #eee;
  overflow: hidden;
}

.inspector-signal-bar-fill {
  height: 100%;
  background: #4a6fa5;
  border-radius: 999px;
  transition: width 0.3s ease;
}

.inspector-signal-bar-na {
  width: 100%;
  background: repeating-linear-gradient(45deg, #ccc, #ccc 6px, #ddd 6px, #ddd 12px);
}

@keyframes inspector-signal-bar-flash {
  0% {
    box-shadow: 0 0 0 0 rgba(74, 111, 165, 0.7);
    filter: brightness(1.35);
  }

  70% {
    box-shadow: 0 0 0 5px rgba(74, 111, 165, 0);
  }

  100% {
    box-shadow: 0 0 0 0 rgba(74, 111, 165, 0);
    filter: brightness(1);
  }
}

.inspector-signal-bar-changed {
  animation: inspector-signal-bar-flash 0.9s ease-out;
}

.autotracking-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #999;
  background: white;
  color: #666;
  cursor: pointer;
  font-size: 0.82rem;
}

.autotracking-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.autotracking-btn-on {
  border-color: #2e7d32;
  background: #2e7d32;
  color: white;
}

.autotracking-btn-on:hover:not(:disabled) {
  background: #256428;
}

.autotracking-btn:disabled {
  opacity: 0.6;
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
