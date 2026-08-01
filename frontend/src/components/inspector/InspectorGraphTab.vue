<script setup>
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import cytoscape from 'cytoscape'
import { getProjectGraph } from '../../api.js'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'

const props = defineProps({
  projectName: { type: String, required: true },
  highlightedStateKey: { type: String, default: null },
  autoJumpOnHighlightChange: { type: Boolean, default: false },
  nextActionEdge: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  annotatable: { type: Boolean, default: false },
  expectedState: { type: String, default: null }
})

const emit = defineEmits(['jump-to-definition', 'select-attachment', 'update-expected-state'])

// The dropdown's own "<not labelled>" option — an explicit, distinct
// choice from every real state key, so leaving it selected never looks
// like "the expert confirmed this state," only like "nobody has looked
// yet." Selecting it clears the annotation, same as the × button.
const UNLABELLED = ''

function onExpectedStateChange(rawValue) {
  emit('update-expected-state', rawValue === UNLABELLED ? null : rawValue)
}

// The Inspector is always inside a narrow, `overflow: hidden` split-view
// panel — see useFloatingTooltip's own docstring for why the (?) tooltip
// needs this instead of a normal absolutely-positioned one (or the
// browser's native `title`, which wasn't rendering reliably either).
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
const selectedElement = ref(null)
const graphNodes = ref([])
const graphEdges = ref([])

function attachmentLabel(index) { return String.fromCharCode(97 + index) }

const isSelectedActionNext = computed(() => {
  if (selectedElement.value?.kind !== 'action' || !props.nextActionEdge) return false
  return selectedElement.value.data.source === props.nextActionEdge.stateKey && selectedElement.value.data.actionName === props.nextActionEdge.actionName
})

const isSelectedActionFired = computed(() => {
  if (selectedElement.value?.kind !== 'action' || !props.firedActionEdge) return false
  return selectedElement.value.data.source === props.firedActionEdge.stateKey && selectedElement.value.data.actionName === props.firedActionEdge.actionName
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

function nodeToCyData(n) { return { id: n.key, uiLabel: n.ui_label, uiDescription: n.ui_description, final: n.final, isStart: n.is_start, chat: n.chat, onEnter: n.on_enter, historyCutoff: n.history_cutoff, transitionLogLevel: n.transition_log_level, attachments: n.attachments } }
function edgeToCyData(e, id) { return { id, source: e.source, target: e.target, uiLabel: e.ui_label, uiDescription: e.ui_description, actionName: e.action_name, buttonText: e.ui_button, trigger: e.trigger, hasTrigger: e.has_trigger, actionPrompt: e.action_prompt } }

function graphElements(nodes, edges) {
  return [...nodes.map(n => ({ data: nodeToCyData(n) })), ...edges.map((e, i) => ({ data: edgeToCyData(e, `edge-${i}`) }))]
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

function selectAttachment(fileName) { emit('select-attachment', fileName) }

function renderGraph(nodes, edges) {
  destroyGraph()
  if (!graphHost.value) return
  const startKey = nodes.find(n => n.is_start)?.key
  cyGraph = cytoscape({
    container: graphHost.value,
    elements: graphElements(nodes, edges),
    style: [
      { selector: 'node', style: { 'background-color': '#eef2f9', 'border-width': 2, 'border-color': '#4a6fa5', label: 'data(uiLabel)', 'text-valign': 'center', 'text-halign': 'center', 'font-size': '9px', color: '#333', shape: 'round-rectangle', width: 'label', height: 'label', padding: '8px', 'text-wrap': 'wrap', 'text-max-width': '80px' } },
      { selector: 'node[?final]', style: { 'border-width': 4, 'border-color': '#c62828', 'background-color': '#fdecea' } },
      { selector: 'node[?isStart]', style: { 'border-color': '#2e7d32', 'background-color': '#eaf6ea' } },
      { selector: 'node.current-state', style: { 'overlay-color': '#f5a623', 'overlay-opacity': 0.35, 'overlay-padding': 6 } },
      { selector: 'edge', style: { width: 1.5, 'line-color': '#9ab0cc', 'target-arrow-color': '#9ab0cc', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8, 'curve-style': 'bezier', label: 'data(uiLabel)', 'font-size': '7px', color: '#666', 'text-background-color': 'white', 'text-background-opacity': 0.85, 'text-background-padding': '2px', 'text-wrap': 'wrap', 'text-max-width': '70px' } },
      { selector: 'edge[!hasTrigger]', style: { 'line-style': 'dashed' } },
      { selector: 'edge.next-action', style: { 'line-color': '#2e7d32', 'target-arrow-color': '#2e7d32', width: 2.5 } },
      { selector: 'edge.fired-action', style: { 'line-color': '#ad1457', 'target-arrow-color': '#ad1457', width: 3 } },
      { selector: 'edge[source = target]', style: { 'curve-style': 'loop', 'loop-direction': '-45deg', 'loop-sweep': '45deg' } }
    ],
    layout: { name: 'breadthfirst', directed: true, roots: startKey ? [startKey] : undefined, padding: 16, spacingFactor: 1.1 }
  })
  cyGraph.on('tap', 'node', handleNodeTap)
  cyGraph.on('tap', 'edge', handleEdgeTap)
  cyGraph.on('tap', (evt) => { if (evt.target === cyGraph) closeGraphDetail() })

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
  } catch {} finally {
    graphLoading.value = false
  }
}

function applyCurrentStateHighlight() {
  if (!cyGraph) return
  cyGraph.nodes().removeClass('current-state')
  if (props.highlightedStateKey != null) cyGraph.nodes().filter(n => n.id() === props.highlightedStateKey).addClass('current-state')
}

function applyNextActionHighlight() {
  if (!cyGraph) return
  cyGraph.edges().removeClass('next-action')
  if (props.nextActionEdge) cyGraph.edges().filter(e => e.data('source') === props.nextActionEdge.stateKey && e.data('actionName') === props.nextActionEdge.actionName).addClass('next-action')
}

function applyFiredActionHighlight() {
  if (!cyGraph) return
  cyGraph.edges().removeClass('fired-action')
  if (props.firedActionEdge) cyGraph.edges().filter(e => e.data('source') === props.firedActionEdge.stateKey && e.data('actionName') === props.firedActionEdge.actionName).addClass('fired-action')
}

function syncSelectionToHighlightedState({ emitJump = false } = {}) {
  const key = props.highlightedStateKey
  const node = key == null ? null : graphNodes.value.find((n) => n.key === key)
  if (!node) return selectedElement.value = null
  selectGraphElement('state', nodeToCyData(node))
  if (emitJump) emit('jump-to-definition', { kind: 'state', stateKey: node.key })
}

function resize() { cyGraph?.resize() }
function fit() { cyGraph?.fit() }

watch(() => props.highlightedStateKey, () => { applyCurrentStateHighlight(); syncSelectionToHighlightedState({ emitJump: props.autoJumpOnHighlightChange }) })
watch(() => props.nextActionEdge, applyNextActionHighlight, { deep: true })
watch(() => props.firedActionEdge, applyFiredActionHighlight, { deep: true })

defineExpose({ loadGraph, resize, fit })

onMounted(async () => {
  await nextTick()
  await loadGraph()
})
onBeforeUnmount(destroyGraph)
</script>

<template>
  <div class="inspector-graph-section">
    <div class="inspector-graph-host-wrap">
      <p v-if="graphLoading" class="signals-status inspector-graph-status">Loading…</p>
      <div ref="graphHost" class="inspector-graph-host"></div>
    </div>

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
          'inspector-annotation-select-correct': expectedState != null && expectedState === highlightedStateKey,
          'inspector-annotation-select-incorrect': expectedState != null && expectedState !== highlightedStateKey
        }"
        :value="expectedState ?? UNLABELLED"
        @change="onExpectedStateChange($event.target.value)"
      >
        <option :value="UNLABELLED" class="inspector-annotation-option-unlabelled">&lt;not labelled&gt;</option>
        <option v-for="node in graphNodes" :key="node.key" :value="node.key">{{ node.ui_label }}</option>
      </select>
    </div>

    <div v-if="selectedElement" class="inspector-detail-card">
      <div class="inspector-detail-header">
        <div class="inspector-detail-header-top">
          <span class="inspector-detail-badge" :class="selectedElement.kind === 'state' ? 'inspector-detail-badge-state' : 'inspector-detail-badge-action'">{{ selectedElement.kind === 'state' ? 'State' : 'Action' }}</span>
          <span class="inspector-detail-title">{{ selectedElement.data.uiLabel }}</span>
          <button class="close-x-btn" title="Close" @click="closeGraphDetail">×</button>
        </div>
        <div v-if="hasSelectedElementBadges" class="inspector-detail-badges">
          <template v-if="selectedElement.kind === 'state'">
            <span v-if="isSelectedStateCurrent" class="inspector-detail-badge inspector-detail-badge-current">Current</span>
            <span v-if="selectedElement.data.isStart" class="inspector-detail-badge inspector-detail-badge-start">Start</span>
            <span v-if="selectedElement.data.final" class="inspector-detail-badge inspector-detail-badge-final">Final</span>
            <span v-if="!selectedElement.data.chat" class="inspector-detail-badge inspector-detail-badge-neutral">No chat</span>
            <span v-if="selectedElement.data.historyCutoff" class="inspector-detail-badge inspector-detail-badge-neutral">History cutoff</span>
          </template>
          <template v-else>
            <span v-if="isSelectedActionNext" class="inspector-detail-badge inspector-detail-badge-next">Next</span>
            <span v-if="isSelectedActionFired" class="inspector-detail-badge inspector-detail-badge-fired">Fired</span>
            <span v-if="!selectedElement.data.hasTrigger" class="inspector-detail-badge inspector-detail-badge-manual">Manual</span>
          </template>
        </div>
      </div>
      <div class="inspector-detail-body">
        <template v-if="selectedElement.kind === 'state'">
          <p v-if="selectedElement.data.uiDescription" class="inspector-detail-ui_description">{{ selectedElement.data.uiDescription }}</p>
          <p v-if="selectedElement.data.onEnter" class="inspector-detail-field"><strong>On enter:</strong> {{ selectedElement.data.onEnter }}</p>
        </template>
        <template v-else>
          <p v-if="selectedElement.data.uiDescription" class="inspector-detail-ui_description">{{ selectedElement.data.uiDescription }}</p>
          <p class="inspector-detail-field"><strong>{{ selectedElement.data.source }}</strong> → <strong>{{ selectedElement.data.target }}</strong></p>
          <p v-if="selectedElement.data.buttonText" class="inspector-detail-field"><strong>Button:</strong> {{ selectedElement.data.buttonText }}</p>
          <p v-if="selectedElement.data.trigger" class="inspector-detail-field"><strong>Trigger:</strong><code class="inspector-detail-code">{{ selectedElement.data.trigger }}</code></p>
          <p v-if="selectedElement.data.actionPrompt" class="inspector-detail-field"><strong>Action prompt:</strong> {{ selectedElement.data.actionPrompt }}</p>
        </template>
        <div v-if="editableFiles && selectedElement.data.attachments?.length" class="inspector-attachments">
          <button v-for="(fileName, idx) in selectedElement.data.attachments" :key="fileName" class="inspector-attachment-btn" :class="{ 'inspector-attachment-btn-disabled': !editableFiles.includes(fileName) }" :disabled="!editableFiles.includes(fileName)" :title="editableFiles.includes(fileName) ? fileName : `${fileName} (not text-editable)`" @click.stop="selectAttachment(fileName)">{{ attachmentLabel(idx) }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inspector-graph-section { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.inspector-annotation-bar { display: flex; align-items: center; gap: 0.4rem; margin-top: 0.6rem; flex-shrink: 0; }
.inspector-annotation-label { font-size: 0.78rem; color: #666; }
.inspector-annotation-select { flex: 1; min-width: 0; padding: 0.3rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; background: white; font-size: 0.82rem; color: #999; }
/* An explicit choice matching the actual/current state — same green used
   for a "correct" transition marker in the chat timeline (see
   BenchmarkProjectView.vue's own .benchmark-transition-row-correct). */
.inspector-annotation-select-correct { border-color: #2e7d32; background: #e8f5e9; color: #333; }
/* An explicit choice that differs from it — same red as the timeline's
   own .benchmark-transition-row-incorrect. */
.inspector-annotation-select-incorrect { border-color: #c62828; background: #fdecea; color: #333; }
.inspector-annotation-option-unlabelled { color: #999; font-style: italic; }
.inspector-annotation-help { position: relative; flex-shrink: 0; width: 1.2rem; height: 1.2rem; border-radius: 50%; border: 1px solid #999; color: #666; font-size: 0.7rem; display: flex; align-items: center; justify-content: center; cursor: help; }
/* Teleported to <body> and positioned in viewport coordinates (see
   showHelpTooltip) — position: fixed here, not absolute, since it's no
   longer nested inside the icon: the Inspector's own split-view panel
   clips anything positioned relative to content inside it. */
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
.inspector-detail-card { flex-shrink: 0; margin-top: 0.75rem; max-height: 45%; display: flex; flex-direction: column; border-radius: 8px; border: 1px solid #eee; background: #fafafa; overflow: hidden; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badges { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-state { background: #4a6fa5; }
.inspector-detail-badge-action { background: #8a6d3b; }
.inspector-detail-badge-current { background: #f5a623; color: #3a2600; }
.inspector-detail-badge-start, .inspector-detail-badge-next { background: #2e7d32; }
.inspector-detail-badge-fired { background: #ad1457; }
.inspector-detail-badge-final { background: #c62828; }
.inspector-detail-badge-manual { background: #5c6b7a; }
.inspector-detail-badge-neutral { background: #8a8a8a; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.close-x-btn { flex-shrink: 0; width: 1.4rem; height: 1.4rem; line-height: 1; border: none; border-radius: 6px; background: none; color: #666; cursor: pointer; font-size: 1rem; }
.close-x-btn:hover { background: #eee; }
.inspector-detail-body { padding: 0.6rem 0.75rem; overflow-y: auto; font-size: 0.8rem; color: #444; }
.inspector-detail-ui_description { margin: 0 0 0.5rem; line-height: 1.4; }
.inspector-detail-field { margin: 0 0 0.4rem; line-height: 1.4; }
.inspector-detail-code { font-size: 0.75rem; background: #eee; border-radius: 4px; padding: 0.1rem 0.4rem; }
.inspector-attachments { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.5rem; }
.inspector-attachment-btn { width: 1.5rem; height: 1.5rem; line-height: 1; border-radius: 4px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; font-size: 0.72rem; font-weight: 600; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.inspector-attachment-btn:hover:not(:disabled) { background: #4a6fa5; color: white; }
.inspector-attachment-btn-disabled { border-color: #ccc; color: #aaa; cursor: not-allowed; }
.inspector-attachment-btn-disabled:hover { background: white; color: #aaa; }
</style>
