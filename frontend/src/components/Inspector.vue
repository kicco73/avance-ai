<script setup>
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import cytoscape from 'cytoscape'
import { getProjectSignals, getProjectGraph, getMetrics } from '../api.js'
import { hasSignalValue, useSignalChangeFlash } from '../signalDisplay.js'
import { renderMarkdown } from '../markdown.js'

// Mode-agnostic core of the state-graph/Signals/Metrics/Model Inspector —
// shared by EditProjectView.vue (live, editable project) and
// BenchmarkView.vue (read-only, point-in-time review of a past session).
// Everything mode-specific — which state counts as "current", where live
// signal values/metrics come from, and what a click on a definition
// should do (jump to the YAML source vs. nothing) — is a prop/emit the
// parent owns; this component never fetches live conversation data
// itself, only the project's own static definitions (graph, signal
// definitions) plus metrics (optionally point-in-time, see
// `untilMessageId`).
const props = defineProps({
  projectName: { type: String, required: true },
  // The state to highlight as "current" on the graph (green glow) — the
  // live conversation's state for EditProjectView, or the state as of
  // the selected point in time for BenchmarkView.
  highlightedStateKey: { type: String, default: null },
  // When true, a highlightedStateKey change also emits jump-to-definition
  // (EditProjectView's cursor-follows-the-live-state behavior). Benchmark
  // mode has no YAML source to jump to, so it leaves this false — the
  // graph/detail-card selection still follows the highlight either way.
  autoJumpOnHighlightChange: { type: Boolean, default: false },
  // { stateKey, actionName } highlighted green as "would fire next" from
  // the live current state — EditProjectView-only concept.
  nextActionEdge: { type: Object, default: null },
  // { stateKey, actionName } highlighted as "the action that produced
  // this transition" — BenchmarkView-only concept, its own distinct color.
  firedActionEdge: { type: Object, default: null },
  // { [signalName]: { value, error } } — live signal values, wherever the
  // parent gets them from (a live API poll, or reconstructed from a
  // loaded session timeline).
  signalValues: { type: Object, default: () => ({}) },
  // When set, Metrics are computed as of this message's own timestamp
  // instead of the live/current history — see api.js's getMetrics.
  untilMessageId: { type: [Number, String], default: null },
  showModelTab: { type: Boolean, default: false },
  activeModel: { type: Object, default: null },
  // Hides the header's own × button when false — BenchmarkView's
  // Inspector is always visible, nothing to collapse.
  closable: { type: Boolean, default: true },
  // Text-editable file names (see project_service.TEXT_EDITABLE_EXTENSIONS)
  // — enables the attachment buttons in the detail card/Signals tab and
  // their disabled state. null (BenchmarkView's default) hides them
  // entirely: there's no file explorer to jump an attachment into.
  editableFiles: { type: Array, default: null }
})

const emit = defineEmits(['jump-to-definition', 'select-attachment', 'close'])

function attachmentLabel(index) {
  return String.fromCharCode(97 + index)
}

const inspectorTab = ref('graph') // 'graph' | 'signals' | 'metrics' | 'model'

const signalsLoading = ref(true)
const signals = ref([])

const metricsLoading = ref(false)
const metrics = ref([])
const { recentlyChanged: recentlyChangedMetrics, markChanged: markMetricsChanged } = useSignalChangeFlash()

const { recentlyChanged: recentlyChangedSignals, markChanged: markSignalsChanged } = useSignalChangeFlash()
watch(
  () => props.signalValues,
  (nextValues, previousValues) => {
    const previous = Object.entries(previousValues || {}).map(([name, v]) => ({ name, ...v }))
    const next = Object.entries(nextValues || {}).map(([name, v]) => ({ name, ...v }))
    markSignalsChanged(previous, next)
  }
)

const graphLoading = ref(true)
const graphHost = ref(null)
let cyGraph = null

// The state or action last tapped in the graph — { kind: 'state'|'action',
// data } or null. Non-null shrinks the graph box to make room for its
// detail card below (see .inspector-graph-section's flex layout).
const selectedElement = ref(null)

// Raw graph data (as fetched, not the cytoscape-shaped elements) — kept
// around so syncSelectionToHighlightedState can look a key up without
// re-fetching, the same way a click already has its node's data on hand.
const graphNodes = ref([])
const graphEdges = ref([])

const isSelectedActionNext = computed(() => {
  if (selectedElement.value?.kind !== 'action' || !props.nextActionEdge) return false
  return (
    selectedElement.value.data.source === props.nextActionEdge.stateKey &&
    selectedElement.value.data.actionName === props.nextActionEdge.actionName
  )
})

const isSelectedActionFired = computed(() => {
  if (selectedElement.value?.kind !== 'action' || !props.firedActionEdge) return false
  return (
    selectedElement.value.data.source === props.firedActionEdge.stateKey &&
    selectedElement.value.data.actionName === props.firedActionEdge.actionName
  )
})

const isSelectedStateCurrent = computed(() => {
  return selectedElement.value?.kind === 'state' && selectedElement.value.data.id === props.highlightedStateKey
})

const hasSelectedElementBadges = computed(() => {
  if (!selectedElement.value) return false
  if (selectedElement.value.kind === 'state') {
    const d = selectedElement.value.data
    return isSelectedStateCurrent.value || d.isStart || d.final || !d.chat || d.historyCutoff
  }
  return isSelectedActionNext.value || isSelectedActionFired.value || !selectedElement.value.data.hasTrigger
})

function destroyGraph() {
  cyGraph?.destroy()
  cyGraph = null
}

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

function selectGraphElement(kind, data) {
  selectedElement.value = { kind, data }
  nextTick(() => cyGraph?.resize())
}

function closeGraphDetail() {
  selectedElement.value = null
  nextTick(() => cyGraph?.resize())
}

function handleNodeTap(evt) {
  const data = evt.target.data()
  selectGraphElement('state', data)
  emit('jump-to-definition', { kind: 'state', stateKey: data.id })
}

function handleEdgeTap(evt) {
  const data = evt.target.data()
  selectGraphElement('action', data)
  emit('jump-to-definition', { kind: 'action', stateKey: data.source, actionName: data.actionName })
}

function selectAttachment(fileName) {
  emit('select-attachment', fileName)
}

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
        // The highlighted ("current") state — an overlay glow rather than
        // a border/background change, so it composes cleanly with
        // final/start's own colors instead of fighting them for the same
        // visual channel (see syncSelectionToHighlightedState/
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
        // The action that would fire next from the live current state
        // (EditProjectView only — see nextActionEdge/applyNextActionHighlight).
        selector: 'edge.next-action',
        style: {
          'line-color': '#2e7d32',
          'target-arrow-color': '#2e7d32',
          width: 2.5
        }
      },
      {
        // The action that actually fired, producing a clicked transition
        // (BenchmarkView only — see firedActionEdge/applyFiredActionHighlight)
        // — same magenta as the "Metric" badge, kept visually distinct
        // from next-action's green so the two concepts never look alike.
        selector: 'edge.fired-action',
        style: {
          'line-color': '#ad1457',
          'target-arrow-color': '#ad1457',
          width: 3
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
  // also the moment a freshly (re)loaded Inspector needs to catch up with
  // its own props — re-sync/re-highlight either way. No jump-to-definition
  // here: unlike an actual highlight change (see the watcher below), a
  // reload triggered by an unrelated save/mount must not yank focus away
  // from whatever the user is doing.
  applyCurrentStateHighlight()
  applyNextActionHighlight()
  applyFiredActionHighlight()
  syncSelectionToHighlightedState()
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

async function loadMetrics() {
  metricsLoading.value = true
  try {
    const nextMetrics = await getMetrics(props.untilMessageId ?? undefined)
    markMetricsChanged(metrics.value, nextMetrics)
    metrics.value = nextMetrics
  } catch {
    // already surfaced via apiFetch
  } finally {
    metricsLoading.value = false
  }
}

function applyCurrentStateHighlight() {
  if (!cyGraph) return
  cyGraph.nodes().removeClass('current-state')
  const key = props.highlightedStateKey
  if (key == null) return
  cyGraph.nodes().filter((n) => n.id() === key).addClass('current-state')
}

function applyNextActionHighlight() {
  if (!cyGraph) return
  cyGraph.edges().removeClass('next-action')
  if (!props.nextActionEdge) return
  cyGraph
    .edges()
    .filter((edge) => edge.data('source') === props.nextActionEdge.stateKey && edge.data('actionName') === props.nextActionEdge.actionName)
    .addClass('next-action')
}

function applyFiredActionHighlight() {
  if (!cyGraph) return
  cyGraph.edges().removeClass('fired-action')
  if (!props.firedActionEdge) return
  cyGraph
    .edges()
    .filter((edge) => edge.data('source') === props.firedActionEdge.stateKey && edge.data('actionName') === props.firedActionEdge.actionName)
    .addClass('fired-action')
}

// Mirrors what tapping highlightedStateKey's own node in the graph would
// do — same selection (and, when `emitJump` is true, the same
// jump-to-definition emit) — so the Inspector automatically tracks
// whatever the parent says is "current" right now, not just clicks made
// inside the graph itself.
function syncSelectionToHighlightedState({ emitJump = false } = {}) {
  const key = props.highlightedStateKey
  const node = key == null ? null : graphNodes.value.find((n) => n.key === key)
  if (!node) {
    selectedElement.value = null
    return
  }
  selectGraphElement('state', nodeToCyData(node))
  if (emitJump) emit('jump-to-definition', { kind: 'state', stateKey: node.key })
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
  } else if (tab === 'metrics') {
    loadMetrics()
  }
}

watch(
  () => props.highlightedStateKey,
  () => {
    applyCurrentStateHighlight()
    syncSelectionToHighlightedState({ emitJump: props.autoJumpOnHighlightChange })
  }
)
watch(() => props.nextActionEdge, applyNextActionHighlight, { deep: true })
watch(() => props.firedActionEdge, applyFiredActionHighlight, { deep: true })

// Reloads the project's own definitions (graph + signals) — called by a
// parent that just saved an edit (EditProjectView); nothing to reload for
// a read-only viewer of a fixed project (BenchmarkView never calls this).
async function refresh() {
  await Promise.all([loadSignals(), loadGraph()])
}

// Refreshes the Metrics tab only while it's the one actually visible —
// called by a parent whenever whatever the Metrics tab depends on changes
// (EditProjectView: a new turn; BenchmarkView: a different message
// selected) — metrics are otherwise never prefetched in the background,
// since computing them means loading the whole message/session/signal
// history (see metrics_framework/README.md #16: no caching).
async function refreshMetrics() {
  if (inspectorTab.value === 'metrics') await loadMetrics()
}

function resize() {
  cyGraph?.resize()
}

defineExpose({ refresh, refreshMetrics, resize })

onMounted(async () => {
  await nextTick() // graphHost only exists once this component has mounted
  await Promise.all([loadSignals(), loadGraph()])
})
onBeforeUnmount(destroyGraph)
</script>

<template>
  <div class="inspector-header">
    <span class="inspector-title">Inspector</span>
    <button v-if="closable" class="close-x-btn" title="Close" @click="emit('close')">×</button>
  </div>
  <div class="inspector-tabs">
    <button
      class="inspector-tab-btn"
      :class="{ 'inspector-tab-btn-active': inspectorTab === 'graph' }"
      @click="setInspectorTab('graph')"
    >
      States
    </button>
    <button
      class="inspector-tab-btn"
      :class="{ 'inspector-tab-btn-active': inspectorTab === 'signals' }"
      @click="setInspectorTab('signals')"
    >
      Signals
    </button>
    <button
      class="inspector-tab-btn"
      :class="{ 'inspector-tab-btn-active': inspectorTab === 'metrics' }"
      @click="setInspectorTab('metrics')"
    >
      Metrics
    </button>
    <button
      v-if="showModelTab"
      class="inspector-tab-btn"
      :class="{ 'inspector-tab-btn-active': inspectorTab === 'model' }"
      @click="setInspectorTab('model')"
    >
      Model
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
            <span
              class="inspector-detail-badge"
              :class="selectedElement.kind === 'state' ? 'inspector-detail-badge-state' : 'inspector-detail-badge-action'"
            >
              {{ selectedElement.kind === 'state' ? 'State' : 'Action' }}
            </span>
            <span class="inspector-detail-title">{{ selectedElement.data.uiLabel }}</span>
            <button class="close-x-btn" title="Close" @click="closeGraphDetail">×</button>
          </div>

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
              <span v-if="isSelectedActionFired" class="inspector-detail-badge inspector-detail-badge-fired">
                Fired
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

          <div v-if="editableFiles && selectedElement.data.attachments?.length" class="inspector-attachments">
            <button
              v-for="(fileName, idx) in selectedElement.data.attachments"
              :key="fileName"
              class="inspector-attachment-btn"
              :class="{ 'inspector-attachment-btn-disabled': !editableFiles.includes(fileName) }"
              :disabled="!editableFiles.includes(fileName)"
              :title="editableFiles.includes(fileName) ? fileName : `${fileName} (not text-editable)`"
              @click.stop="selectAttachment(fileName)"
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
          class="inspector-signal-block"
          :class="{ 'inspector-signal-block-clickable': editableFiles }"
          :title="editableFiles ? 'Jump to definition' : undefined"
          @click="editableFiles && emit('jump-to-definition', { kind: 'signal', signalName: signal.name })"
        >
          <div class="inspector-signal-header">
            <span class="inspector-detail-badge inspector-detail-badge-signal">Signal</span>
            <span class="inspector-signal-name">{{ signal.ui_label || signal.name }}</span>
          </div>
          <span v-if="signal.ui_description" class="inspector-signal-ui_description">
            {{ signal.ui_description }}
          </span>

          <div v-if="editableFiles && signal.attachments?.length" class="inspector-attachments">
            <button
              v-for="(fileName, idx) in signal.attachments"
              :key="fileName"
              class="inspector-attachment-btn"
              :class="{ 'inspector-attachment-btn-disabled': !editableFiles.includes(fileName) }"
              :disabled="!editableFiles.includes(fileName)"
              :title="editableFiles.includes(fileName) ? fileName : `${fileName} (not text-editable)`"
              @click.stop="selectAttachment(fileName)"
            >
              {{ attachmentLabel(idx) }}
            </button>
          </div>

          <div class="inspector-signal-bar-track">
            <div
              v-if="hasSignalValue(signalValues[signal.name])"
              class="inspector-signal-bar-fill"
              :class="{ 'inspector-signal-bar-changed': recentlyChangedSignals.has(signal.name) }"
              :style="{ width: signalValues[signal.name].value + '%' }"
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

    <div v-show="inspectorTab === 'metrics'" class="inspector-metrics-section">
      <p v-if="metricsLoading" class="signals-status">Loading…</p>
      <p v-else-if="!metrics.length" class="signals-status">No metrics computed yet.</p>
      <div v-else class="inspector-signal-list">
        <div v-for="metric in metrics" :key="metric.name" class="inspector-signal-block">
          <div class="inspector-signal-header">
            <span class="inspector-detail-badge inspector-detail-badge-metric">Metric</span>
            <span class="inspector-signal-name">{{ metric.ui_label || metric.name }}</span>
          </div>
          <span v-if="metric.ui_description" class="inspector-signal-ui_description">
            {{ metric.ui_description }}
          </span>

          <div class="inspector-signal-bar-track">
            <div
              class="inspector-signal-bar-fill"
              :class="{ 'inspector-signal-bar-changed': recentlyChangedMetrics.has(metric.name) }"
              :style="{ width: metric.value + '%' }"
            ></div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showModelTab" v-show="inspectorTab === 'model'" class="inspector-model-section">
      <p v-if="!activeModel" class="signals-status">No AI model configured.</p>
      <div v-else class="inspector-signal-block">
        <div class="inspector-signal-header">
          <span class="inspector-detail-badge inspector-detail-badge-model">Model</span>
          <span class="inspector-signal-name">{{ activeModel.ui_label }}</span>
        </div>
        <br/>
        <p class="inspector-detail-field"><strong>Driver:</strong> {{ activeModel.driver }}</p>
        <p class="inspector-detail-field"><strong>Model:</strong> {{ activeModel.model }}</p>
        <p v-if="activeModel.url" class="inspector-detail-field"><strong>Url:</strong> {{ activeModel.url }}</p>
        <br/>
        <div
          v-if="activeModel.ui_description"
          class="inspector-model-description"
          v-html="renderMarkdown(activeModel.ui_description)"
        ></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
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

.inspector-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
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

.inspector-detail-badge-model {
  background: #2f8f83;
}

.inspector-detail-badge-metric {
  background: #ad1457;
}

.inspector-detail-badge-current {
  background: #f5a623;
  color: #3a2600;
}

.inspector-detail-badge-start,
.inspector-detail-badge-next {
  background: #2e7d32;
}

.inspector-detail-badge-fired {
  /* Same magenta as the fired-action edge/Metric badge — one hue for
     "this is what actually happened", distinct from Next's green
     ("this is what would happen live"). */
  background: #ad1457;
}

.inspector-detail-badge-final {
  background: #c62828;
}

.inspector-detail-badge-manual {
  background: #5c6b7a;
}

.inspector-detail-badge-neutral {
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

.inspector-metrics-section {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.inspector-model-section {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.inspector-model-description {
  margin: 0 0 0.5rem;
  line-height: 1.4;
}

.inspector-model-description :deep(p) {
  margin: 0 0 0.4rem;
}

.inspector-model-description :deep(p:last-child) {
  margin-bottom: 0;
}

.inspector-model-description :deep(ul),
.inspector-model-description :deep(ol) {
  margin: 0 0 0.4rem;
  padding-left: 1.2rem;
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
</style>
