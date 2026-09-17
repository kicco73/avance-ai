<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import cytoscape from 'cytoscape'
import { getProjectGraph } from '../../api.js'
import { useFloatingTooltip } from '../../../../useFloatingTooltip.js'

const props = defineProps({
  projectId: { type: String, required: true },
  highlightedStateKey: { type: String, default: null },
  autoJumpOnHighlightChange: { type: Boolean, default: false },
  firedActionEdge: { type: Object, default: null },
  selectedElement: { type: Object, default: null },
  annotatable: { type: Boolean, default: false },
  expectedState: { type: String, default: null },
  imported: { type: Boolean, default: false },
  sessionId: { type: [Number, String], default: null }
})

const emit = defineEmits(['jump-to-definition', 'update-expected-state', 'select'])

const PSEUDO_START_ID = '__avance_init_pseudo_node__'

const UNLABELLED = ''

function onExpectedStateChange(rawValue) {
  emit('update-expected-state', rawValue === UNLABELLED ? null : rawValue)
}

const {
  triggerRef: helpIconRef,
  visible: helpTooltipVisible,
  style: helpTooltipStyle,
  show: showHelpTooltip,
  hide: hideHelpTooltip
} = useFloatingTooltip()

const graphLoading = ref(true)
const graphHost = ref(null)
let cyGraph = null
const graphNodes = ref([])
const graphEdges = ref([])
const graphRevision = ref(null)

function destroyGraph() {
  cyGraph?.destroy()
  cyGraph = null
}

function nodeToCyData(n) { return { id: n.state.key, uiLabel: n.state.ui_label, uiDescription: n.state.ui_description, final: n.state.final, isStart: n.is_start, chatEnabled: n.state.chat_enabled, historyCutoff: n.history_cutoff, reactionsEnabled: n.reactions_enabled, hasReactions: (n.state.reactions?.length ?? 0) > 0, transitionLogLevel: n.transition_log_level, signalTrackingStrategy: n.signal_tracking_strategy, aiMemoryScope: n.ai_memory_scope, attachments: n.attachments, contextualPrompt: n.contextual_prompt, aiMayReadSources: n.state.ai_may_read_sources ?? [], aiMustReadSources: n.state.ai_must_read_sources ?? [], input: n.state.input ?? [], output: n.state.output ?? [] } }
function edgeToCyData(e, id) {
  const isInitEdge = e.source === ''
  return {
    id,
    source: isInitEdge ? PSEUDO_START_ID : e.source,
    target: e.action.target,
    matchStateKey: e.source,
    uiLabel: e.action.ui_label,
    uiDescription: e.ui_description,
    actionName: isInitEdge ? '' : e.action.name,
    buttonText: e.action.ui_button,
    trigger: e.trigger,
    hasTrigger: e.action.has_trigger,
    task: e.action['task'],
    onExit: e.action['on-exit'],
    env: e.env || {},
    isInitEdge
  }
}

function pseudoStartNodeElements(edges) {
  return edges.some((e) => e.source === '')
    ? [{ data: { id: PSEUDO_START_ID, isPseudoStart: true }, selectable: false, grabbable: false }]
    : []
}

function graphElements(nodes, edges) {
  return [
    ...pseudoStartNodeElements(edges),
    ...nodes.map(n => ({ data: nodeToCyData(n) })),
    ...edges.map((e, i) => ({ data: edgeToCyData(e, `edge-${i}`) }))
  ]
}

function applySelectionHighlight(kind, data) {
  if (!cyGraph) return
  cyGraph.nodes().removeClass('selected-element')
  cyGraph.edges().removeClass('selected-element')
  if (kind === 'state') cyGraph.getElementById(data.id).addClass('selected-element')
  else if (kind === 'action') {
    cyGraph.edges().filter((e) => e.data('matchStateKey') === data.matchStateKey && e.data('actionName') === data.actionName)
      .addClass('selected-element')
  }
}

function selectGraphElement(kind, data) {
  applySelectionHighlight(kind, data)
  emit('select', kind == null ? null : { kind, data })
}

function applySelectedElementHighlight() {
  if (!cyGraph) return
  const element = props.selectedElement
  applySelectionHighlight(element?.kind ?? null, element?.data ?? null)
}

function handleNodeTap(evt) {
  const data = evt.target.data()
  if (data.isPseudoStart) return
  selectGraphElement('state', data)
  emit('jump-to-definition', { kind: 'state', stateKey: data.id })
}

function handleEdgeTap(evt) {
  const data = evt.target.data()
  selectGraphElement('action', data)
  emit('jump-to-definition', { kind: 'action', stateKey: data.matchStateKey, actionName: data.actionName })
}

function renderGraph(nodes, edges) {
  destroyGraph()
  if (!graphHost.value) return
  const startKey = nodes.find(n => n.is_start)?.state.key
  const layoutRoot = edges.some((e) => e.source === '') ? PSEUDO_START_ID : startKey
  cyGraph = cytoscape({
    container: graphHost.value,
    elements: graphElements(nodes, edges),
    style: [
      { selector: 'node', style: { 'background-color': '#eef2f9', 'border-width': 2, 'border-color': '#4a6fa5', label: 'data(uiLabel)', 'text-valign': 'center', 'text-halign': 'center', 'font-size': '9px', color: '#333', shape: 'round-rectangle', width: 'label', height: 'label', padding: '8px', 'text-wrap': 'wrap', 'text-max-width': '80px' } },
      { selector: 'node[?final]', style: { 'border-width': 4, 'border-color': '#c62828', 'background-color': '#fdecea' } },
      { selector: 'node[?isStart]', style: { 'border-color': '#2e7d32', 'background-color': '#eaf6ea' } },
      { selector: 'node.current-state', style: { 'overlay-color': '#f5a623', 'overlay-opacity': 0.35, 'overlay-padding': 6 } },
      { selector: 'node.selected-element', style: { 'border-color': '#2c4d7a', 'border-width': 4, 'background-color': '#dce6f5' } },
      { selector: 'node[?isPseudoStart]', style: { width: 1, height: 1, 'background-opacity': 0, 'border-width': 0, label: '' } },
      { selector: 'edge', style: { width: 1.5, 'line-color': '#9ab0cc', 'target-arrow-color': '#9ab0cc', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8, 'curve-style': 'bezier', label: 'data(uiLabel)', 'font-size': '7px', color: '#666', 'text-background-color': 'white', 'text-background-opacity': 0.85, 'text-background-padding': '2px', 'text-wrap': 'wrap', 'text-max-width': '70px' } },
      { selector: 'edge[!hasTrigger]', style: { 'line-style': 'dashed' } },
      { selector: 'edge[?isInitEdge]', style: { 'line-color': '#2e7d32', 'target-arrow-color': '#2e7d32' } },
      { selector: 'edge.fired-action', style: { 'line-color': '#ad1457', 'target-arrow-color': '#ad1457', width: 3 } },
      { selector: 'edge.selected-element', style: { 'line-color': '#2c4d7a', 'target-arrow-color': '#2c4d7a', width: 3 } },
      { selector: 'edge[source = target]', style: { 'curve-style': 'loop', 'loop-direction': '-45deg', 'loop-sweep': '45deg' } }
    ],
    layout: { name: 'breadthfirst', directed: true, roots: layoutRoot != null ? [layoutRoot] : undefined, padding: 16, spacingFactor: 1.1 }
  })
  if (startKey != null && layoutRoot === PSEUDO_START_ID) {
    const pseudoNode = cyGraph.getElementById(PSEUDO_START_ID)
    const startNode = cyGraph.getElementById(startKey)
    if (pseudoNode.nonempty() && startNode.nonempty()) {
      const from = pseudoNode.position()
      const to = startNode.position()
      pseudoNode.position({ x: from.x + (to.x - from.x) / 2, y: from.y + (to.y - from.y) / 2 })
    }
  }
  cyGraph.on('tap', 'node', handleNodeTap)
  cyGraph.on('tap', 'edge', handleEdgeTap)
  cyGraph.on('tap', (evt) => { if (evt.target === cyGraph) selectGraphElement(null, null) })

  applyCurrentStateHighlight()
  applyFiredActionHighlight()
  syncSelectionToSelection()
  applySelectedElementHighlight()
}

async function loadGraph() {
  graphLoading.value = true
  try {
    const { nodes, edges, revision } = await getProjectGraph(props.projectId, props.sessionId)
    graphNodes.value = nodes
    graphEdges.value = edges
    graphRevision.value = revision
    renderGraph(nodes, edges)
  } catch {} finally {
    graphLoading.value = false
  }
}

function applyCurrentStateHighlight() {
  if (!cyGraph) return
  cyGraph.nodes().removeClass('current-state')
  if (props.highlightedStateKey != null) cyGraph.nodes().filter(n => n.id() === props.highlightedStateKey).addClass('current-state')
}

function applyFiredActionHighlight() {
  if (!cyGraph) return
  cyGraph.edges().removeClass('fired-action')
  if (props.firedActionEdge) cyGraph.edges().filter(e => e.data('matchStateKey') === props.firedActionEdge.stateKey && e.data('actionName') === props.firedActionEdge.actionName).addClass('fired-action')
}

function syncSelectionToSelection({ emitJump = false } = {}) {
  if (props.firedActionEdge && cyGraph) {
    const edge = cyGraph.edges().filter(
      (e) => e.data('matchStateKey') === props.firedActionEdge.stateKey && e.data('actionName') === props.firedActionEdge.actionName
    )
    if (edge.nonempty()) {
      selectGraphElement('action', edge.data())
      if (emitJump) emit('jump-to-definition', { kind: 'action', stateKey: props.firedActionEdge.stateKey, actionName: props.firedActionEdge.actionName })
      return
    }
  }
  const key = props.highlightedStateKey
  if (key == null) return
  const node = graphNodes.value.find((n) => n.state.key === key)
  if (!node) return selectGraphElement(null, null)
  selectGraphElement('state', nodeToCyData(node))
  if (emitJump) emit('jump-to-definition', { kind: 'state', stateKey: node.state.key })
}

function resize() { cyGraph?.resize() }
function fit() { cyGraph?.fit() }

async function refresh(active) {
  await loadGraph()
  if (active) {
    await nextTick()
    cyGraph?.resize()
    cyGraph?.fit()
  }
}

watch(() => props.highlightedStateKey, () => { applyCurrentStateHighlight(); syncSelectionToSelection({ emitJump: props.autoJumpOnHighlightChange }) })
watch(() => props.firedActionEdge, () => { applyFiredActionHighlight(); syncSelectionToSelection({ emitJump: props.autoJumpOnHighlightChange }) }, { deep: true })
watch(() => props.selectedElement, applySelectedElementHighlight, { deep: true })

function stateElementFor(stateKey) {
  const node = graphNodes.value.find((n) => n.state.key === stateKey)
  return node ? { kind: 'state', data: nodeToCyData(node) } : null
}

function actionsForState(stateKey) {
  return graphEdges.value
    .filter((e) => e.source === stateKey)
    .map((e, i) => ({ kind: 'action', data: edgeToCyData(e, `state-actions-${stateKey}-${i}`) }))
}

defineExpose({ loadGraph, resize, fit, refresh, stateElementFor, actionsForState })

onMounted(async () => {
  await nextTick()
  await loadGraph()
})
onBeforeUnmount(destroyGraph)
</script>

<template>
  <div class="inspector-graph-section">
    <div v-if="annotatable" class="inspector-annotation-bar">
      <label class="inspector-annotation-label">Expected state</label>
      <span
        ref="helpIconRef"
        class="inspector-annotation-help"
        tabindex="0"
        @mouseenter="showHelpTooltip"
        @mouseleave="hideHelpTooltip"
        @focus="showHelpTooltip"
        @blur="hideHelpTooltip"
      >
        ?
      </span>
      <Teleport to="body">
        <span v-if="helpTooltipVisible" class="inspector-annotation-help-tooltip-floating" :style="helpTooltipStyle">
          Mark the expected state after this message.
        </span>
      </Teleport>
      <select
        class="inspector-annotation-select"
        :class="{
          'inspector-annotation-select-labelled': imported && expectedState != null,
          'inspector-annotation-select-correct': !imported && expectedState != null && expectedState === highlightedStateKey,
          'inspector-annotation-select-incorrect': !imported && expectedState != null && expectedState !== highlightedStateKey
        }"
        :value="expectedState ?? UNLABELLED"
        @change="onExpectedStateChange($event.target.value)"
      >
        <option :value="UNLABELLED" class="inspector-annotation-option-unlabelled">&lt;not labelled&gt;</option>
        <option v-for="node in graphNodes" :key="node.state.key" :value="node.state.key">{{ node.state.ui_label }}</option>
      </select>
    </div>

    <div class="inspector-graph-host-wrap">
      <p v-if="graphLoading" class="signals-status inspector-graph-status">Loading…</p>
      <div ref="graphHost" class="inspector-graph-host"></div>
      <span v-if="graphRevision != null" class="inspector-graph-revision-badge">Rev. {{ graphRevision }}</span>
    </div>
  </div>
</template>

<style scoped>
.inspector-graph-section { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.inspector-annotation-bar { display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.6rem; flex-shrink: 0; }
.inspector-annotation-label { font-size: 0.78rem; color: #666; }
.inspector-annotation-select { flex: 1; min-width: 0; padding: 0.3rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; background: white; font-size: 0.82rem; color: #999; }
.inspector-annotation-select-correct { border-color: #2e7d32; background: #e8f5e9; color: #333; }
.inspector-annotation-select-incorrect { border-color: #c62828; background: #fdecea; color: #333; }
.inspector-annotation-select-labelled { border-color: #2e7d32; background: #e8f5e9; color: #333; }
.inspector-annotation-option-unlabelled { color: #999; font-style: italic; }
.inspector-annotation-help { position: relative; flex-shrink: 0; width: 1.2rem; height: 1.2rem; border-radius: 50%; border: 1px solid #999; color: #666; font-size: 0.7rem; display: flex; align-items: center; justify-content: center; cursor: help; }
.inspector-annotation-help-tooltip-floating {
  position: fixed;
  width: max-content;
  max-width: 200px;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  background: #333;
  color: white;
  font-size: 0.72rem;
  font-weight: 400;
  line-height: 1.3;
  text-align: left;
  pointer-events: none;
  z-index: 1000;
}
.inspector-graph-host-wrap { position: relative; flex: 1; min-height: 0; border: 1px solid #ddd; border-radius: 8px; background: #fcfcfd; overflow: hidden; }
.inspector-graph-host { width: 100%; height: 100%; }
.inspector-graph-status { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
.inspector-graph-revision-badge {
  position: absolute;
  bottom: 0.5rem;
  right: 0.6rem;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: rgba(74, 111, 165, 0.85);
  color: white;
  font-size: 0.68rem;
  font-weight: 600;
  letter-spacing: 0.02em;
  pointer-events: none;
}
</style>
