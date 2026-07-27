<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Compartment } from '@codemirror/state'
import { EditorView, basicSetup } from 'codemirror'
import { yaml } from '@codemirror/lang-yaml'
import cytoscape from 'cytoscape'
import {
  getProjectFiles,
  getProjectFile,
  putProjectFile,
  deleteProjectFile,
  getProjectSignals,
  getProjectGraph
} from '../api.js'
import { clearApiError, errorDetail, errorMessage, setApiError } from '../errorStore.js'

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
const graphLoading = ref(true)
const graphHost = ref(null)
const inspectorWidth = ref(360)

// A definition clicked in the Inspect panel (graph node/edge or signal
// block) to jump the editor's cursor to, once index.yml is the file open
// in the editor — see jumpToDefinition/applyPendingCursorTarget. Cleared
// once applied, or if the user cancels a pending file-switch dialog.
const pendingCursorTarget = ref(null)

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

// Saves whatever file is currently open, in place. Shared by the header's
// Save button (which also leaves the editor, see save()) and the
// switch-file dialog's "Save" choice (which doesn't). Returns whether it
// succeeded; on failure the shared error store already has the message.
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

async function save() {
  if (await saveCurrentFile()) emit('close')
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

let cyGraph = null

// The state or action last tapped in the graph — { kind: 'state'|'action',
// data } or null. Non-null shrinks the graph box to make room for its
// detail card below (see .inspector-graph-section's flex layout).
const selectedElement = ref(null)

function destroyGraph() {
  cyGraph?.destroy()
  cyGraph = null
}

// Every field the detail card might show travels in the element's own
// cytoscape data, keyed camelCase — so a tap handler can read it straight
// off evt.target.data() with no separate lookup structure to keep in sync.
function graphElements(nodes, edges) {
  return [
    ...nodes.map((n) => ({
      data: {
        id: n.key,
        label: n.label,
        description: n.description,
        final: n.final,
        isStart: n.is_start,
        chat: n.chat,
        onEnter: n.on_enter,
        historyCutoff: n.history_cutoff,
        transitionLogLevel: n.transition_log_level,
        attachments: n.attachments
      }
    })),
    ...edges.map((e, i) => ({
      data: {
        id: `edge-${i}`,
        source: e.source,
        target: e.target,
        label: e.label,
        actionName: e.action_name,
        buttonText: e.button_text,
        trigger: e.trigger,
        hasTrigger: e.has_trigger,
        actionPrompt: e.action_prompt
      }
    }))
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
  // A reload (reopening Inspect, or a save while it's open) can rename/
  // remove the previously selected element, so start clean rather than
  // risk showing stale detail for something that no longer exists.
  selectedElement.value = null
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
          label: 'data(label)',
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
        selector: 'edge',
        style: {
          width: 1.5,
          'line-color': '#9ab0cc',
          'target-arrow-color': '#9ab0cc',
          'target-arrow-shape': 'triangle',
          'arrow-scale': 0.8,
          'curve-style': 'bezier',
          label: 'data(label)',
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
        selector: 'edge[?hasTrigger]',
        style: {
          'line-style': 'dashed',
          'line-color': '#a67c2e',
          'target-arrow-color': '#a67c2e'
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
}

async function loadGraph() {
  graphLoading.value = true
  try {
    const { nodes, edges } = await getProjectGraph(props.projectName)
    renderGraph(nodes, edges)
  } catch {
    // already surfaced via apiFetch
  } finally {
    graphLoading.value = false
  }
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
  await Promise.all([loadSignals(), loadGraph()])
}

// Toggled by the Inspect button and by the panel's own Close button, same
// as SignalsView's autotracking/close pair.
async function toggleInspect() {
  inspecting.value = !inspecting.value
  if (inspecting.value) await openInspect()
  else destroyGraph()
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
    cyGraph?.resize()
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
      <h2>Edit project — {{ projectName }} <span class="edit-project-current-file">/ {{ currentFileName }}</span></h2>
      <div class="edit-project-header-actions">
        <button
          class="inspect-btn"
          :class="{ 'inspect-btn-on': inspecting }"
          @click="toggleInspect"
        >
          Inspect
        </button>
        <button class="save-btn" :disabled="loading || saving" @click="save">
          {{ saving ? 'Saving…' : 'Save' }}
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
        <p v-if="loading" class="edit-project-status">Loading…</p>
        <div v-show="!loading" ref="editorHost" class="edit-project-editor"></div>
      </div>

      <template v-if="inspecting">
        <div class="split-divider inspector-divider" @mousedown="startInspectorDrag"></div>

        <div class="inspector-panel" :style="{ '--inspector-width': inspectorWidth + 'px' }">
          <div class="inspector-header">
            <h3>Inspect</h3>
            <button class="close-btn" @click="toggleInspect">Close</button>
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
                  <span
                    class="inspector-detail-badge"
                    :class="selectedElement.kind === 'state' ? 'inspector-detail-badge-state' : 'inspector-detail-badge-action'"
                  >
                    {{ selectedElement.kind === 'state' ? 'State' : 'Action' }}
                  </span>
                  <span class="inspector-detail-title">{{ selectedElement.data.label }}</span>
                  <button class="inspector-detail-close" title="Close" @click="closeGraphDetail">×</button>
                </div>

                <div class="inspector-detail-body">
                  <template v-if="selectedElement.kind === 'state'">
                    <p v-if="selectedElement.data.description" class="inspector-detail-description">
                      {{ selectedElement.data.description }}
                    </p>
                    <div class="inspector-meta-row">
                      <div class="inspector-detail-flags">
                        <span v-if="selectedElement.data.isStart" class="inspector-detail-flag inspector-detail-flag-start">
                          Start
                        </span>
                        <span v-if="selectedElement.data.final" class="inspector-detail-flag inspector-detail-flag-final">
                          Final
                        </span>
                        <span v-if="!selectedElement.data.chat" class="inspector-detail-flag">No chat</span>
                        <span v-if="selectedElement.data.historyCutoff" class="inspector-detail-flag">
                          History cutoff
                        </span>
                      </div>
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
                    <p v-if="selectedElement.data.onEnter" class="inspector-detail-field">
                      <strong>On enter:</strong> {{ selectedElement.data.onEnter }}
                    </p>
                  </template>
                  <template v-else>
                    <div v-if="selectedElement.data.attachments?.length" class="inspector-meta-row">
                      <div class="inspector-attachments">
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
                  </div>
                  <span v-if="signal.description" class="inspector-signal-description">
                    {{ signal.description }}
                  </span>
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

.edit-project-current-file {
  font-weight: 400;
  color: #666;
  font-size: 0.95rem;
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
  display: flex;
}

.edit-project-status {
  margin: auto;
  color: #444;
}

.edit-project-editor {
  flex: 1;
  min-width: 0;
  border: 1px solid #ddd;
  border-radius: 8px;
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
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #ddd;
}

.inspector-header h3 {
  margin: 0;
  font-size: 1rem;
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
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid #eee;
  flex-shrink: 0;
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

.inspector-detail-close {
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

.inspector-detail-close:hover {
  background: #eee;
}

.inspector-detail-body {
  padding: 0.6rem 0.75rem;
  overflow-y: auto;
  font-size: 0.8rem;
  color: #444;
}

.inspector-detail-description {
  margin: 0 0 0.5rem;
  line-height: 1.4;
}

.inspector-meta-row {
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-bottom: 0.5rem;
}

.inspector-detail-flags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.inspector-detail-flag {
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: #eee;
  color: #555;
}

.inspector-detail-flag-start {
  background: #e3f2e3;
  color: #2e7d32;
}

.inspector-detail-flag-final {
  background: #fdecea;
  color: #c62828;
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

.inspector-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-left: auto;
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

.inspector-signal-description {
  font-size: 0.78rem;
  color: #666;
  line-height: 1.4;
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
