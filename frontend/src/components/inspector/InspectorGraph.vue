<script setup>
// The cytoscape graph itself — mount, tap-to-select, highlight classes,
// flexible-size container — extracted out of InspectorGraphTab.vue so the
// detail card for whatever gets selected (see InspectorDetailCard.vue) can
// be a sibling component instead of markup baked into this one.
// Deliberately has no opinion of its own on what a selection *means*
// beyond emitting it — InspectorGraphTab.vue owns `selectedElement` and
// feeds it to InspectorDetailCard.vue, so closing/resizing that card is
// this component's parent's job, not this one's (see its own resize()).
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import cytoscape from 'cytoscape'
import { getProjectGraph } from '../../api.js'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'

const props = defineProps({
  projectName: { type: String, required: true },
  highlightedStateKey: { type: String, default: null },
  autoJumpOnHighlightChange: { type: Boolean, default: false },
  nextActionEdge: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  annotatable: { type: Boolean, default: false },
  expectedState: { type: String, default: null },
  // Whether the session being annotated was imported (see ChatSession.
  // source) — there's no real avance-computed state to compare
  // expectedState against there (see benchmarkTimeline.js's own
  // transitionAnnotationStatus/resolveTransitionRow, and ChatTimeline.
  // vue's own analogous imported prop), so the select reads as a neutral
  // "labelled" state instead of a correct/incorrect verdict.
  imported: { type: Boolean, default: false }
})

const emit = defineEmits(['jump-to-definition', 'update-expected-state', 'select'])

// Cytoscape rejects an empty string as an element id outright ("Can not
// create element with invalid string ID ``") — this is the pseudo-node's
// *graph-wiring* id only (data.id, and the init edge's own data.source/
// target). The "" chat/Signals.old_state convention (see matchStateKey
// below) is kept as a separate field, purely for highlight-matching
// against firedActionEdge/nextActionEdge, never handed to cytoscape
// itself as an id.
const PSEUDO_START_ID = '__avance_init_pseudo_node__'

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
const graphNodes = ref([])
const graphEdges = ref([])

function destroyGraph() {
  cyGraph?.destroy()
  cyGraph = null
}

// `n`/`e` are the node/edge *wrappers* project_service.py's
// get_project_graph now sends (see its own docstring) — a nested
// StatePayload/ActionPayload (the exact shape chat's own live client
// gets too, see Automaton.get_state_payload/get_action_payload) plus
// whatever extra fields the graph itself needs and those shared payloads
// deliberately don't carry (is_start/history_cutoff/transition_log_level/
// attachments on a node; source/trigger/action_prompt/ui_description on
// an edge — trigger especially never reaches a live chat client, only
// this Inspect-panel-only wrapper).
function nodeToCyData(n) { return { id: n.state.key, uiLabel: n.state.ui_label, uiDescription: n.state.ui_description, final: n.state.final, isStart: n.is_start, chat: n.state.chat, historyCutoff: n.history_cutoff, transitionLogLevel: n.transition_log_level, attachments: n.attachments, contextualPrompt: n.contextual_prompt } }
// The one edge with source === "" is the automaton's own init_action (see
// project_service.py's get_project_graph). Its cytoscape-facing `source`
// is PSEUDO_START_ID (a real node has to exist there — see
// pseudoStartNodeElements), but `matchStateKey` keeps the exact same ""
// the chat's own init-transition already uses everywhere else (Signals.
// action, benchmarkTimeline.js's synthetic entry) — every next/fired-
// action highlight match (see applyFiredActionHighlight/
// isSelectedActionFired) reads *that* field, never cytoscape's own
// source/target, so this one edge needs no special-casing there. Same
// reasoning for actionName: overridden to "" (display-only — the backend
// still reports the real "init_action" name) so {stateKey, actionName}
// lines up with the chat's own transition shape.
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
    actionPrompt: e.action_prompt,
    onEnter: e.action['on-enter'],
    isInitEdge
  }
}

// A transparent node for the init edge's own source — cytoscape needs a
// real node (with a non-empty id — an empty string is rejected outright)
// at each edge endpoint, so the automaton's own "arrow from nowhere" into
// its start state (see get_project_graph's own docstring) is drawn as an
// edge from this invisible pseudo-node (styled zero-size, see
// renderGraph's own style array) rather than a dangling reference. Not
// selectable/grabbable: it's not a real state, just an anchor point.
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

// The single choke point every selection path already funnels through —
// a manual node/edge tap (handleNodeTap/handleEdgeTap), a background tap
// to deselect, and syncSelectionToSelection's own programmatic follow
// (test mode auto-tracking the live state/fired action) alike — so
// applying the visual "selected" class here, rather than separately at
// each call site, keeps every one of them consistent for free. Same
// overlay mechanism as .current-state (see applyCurrentStateHighlight)
// but its own class/color, so a node can show both at once without one
// masking the other — e.g. the live current state, freshly tapped.
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

function handleNodeTap(evt) {
  const data = evt.target.data()
  if (data.isPseudoStart) return // not a real state — nothing to select/jump to
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
  // Rooting the layout at the pseudo-start node itself (when the init
  // edge exists — see pseudoStartNodeElements) rather than the real start
  // state puts that state one level *into* the tree, so its own incoming
  // arrow reads as entering the graph from outside rather than from a
  // node that's simultaneously a root with nothing above it.
  const layoutRoot = edges.some((e) => e.source === '') ? PSEUDO_START_ID : startKey
  cyGraph = cytoscape({
    container: graphHost.value,
    elements: graphElements(nodes, edges),
    style: [
      { selector: 'node', style: { 'background-color': '#eef2f9', 'border-width': 2, 'border-color': '#4a6fa5', label: 'data(uiLabel)', 'text-valign': 'center', 'text-halign': 'center', 'font-size': '9px', color: '#333', shape: 'round-rectangle', width: 'label', height: 'label', padding: '8px', 'text-wrap': 'wrap', 'text-max-width': '80px' } },
      { selector: 'node[?final]', style: { 'border-width': 4, 'border-color': '#c62828', 'background-color': '#fdecea' } },
      { selector: 'node[?isStart]', style: { 'border-color': '#2e7d32', 'background-color': '#eaf6ea' } },
      { selector: 'node.current-state', style: { 'overlay-color': '#f5a623', 'overlay-opacity': 0.35, 'overlay-padding': 6 } },
      // Whatever's actually selected (see selectGraphElement/
      // applySelectionHighlight) — its own border/background, distinct
      // from current-state's overlay above, so both can show on the same
      // node at once (the live current state, freshly tapped) without
      // one masking the other.
      { selector: 'node.selected-element', style: { 'border-color': '#2c4d7a', 'border-width': 4, 'background-color': '#dce6f5' } },
      { selector: 'edge.selected-element', style: { 'line-color': '#2c4d7a', 'target-arrow-color': '#2c4d7a', width: 3 } },
      // The init_action pseudo-node itself is never seen — only the edge
      // leading out of it (styled below) is, reading as an arrow with no
      // visible source.
      { selector: 'node[?isPseudoStart]', style: { width: 1, height: 1, 'background-opacity': 0, 'border-width': 0, label: '' } },
      { selector: 'edge', style: { width: 1.5, 'line-color': '#9ab0cc', 'target-arrow-color': '#9ab0cc', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8, 'curve-style': 'bezier', label: 'data(uiLabel)', 'font-size': '7px', color: '#666', 'text-background-color': 'white', 'text-background-opacity': 0.85, 'text-background-padding': '2px', 'text-wrap': 'wrap', 'text-max-width': '70px' } },
      { selector: 'edge[!hasTrigger]', style: { 'line-style': 'dashed' } },
      { selector: 'edge[?isInitEdge]', style: { 'line-color': '#2e7d32', 'target-arrow-color': '#2e7d32' } },
      { selector: 'edge.next-action', style: { 'line-color': '#2e7d32', 'target-arrow-color': '#2e7d32', width: 2.5 } },
      { selector: 'edge.fired-action', style: { 'line-color': '#ad1457', 'target-arrow-color': '#ad1457', width: 3 } },
      { selector: 'edge[source = target]', style: { 'curve-style': 'loop', 'loop-direction': '-45deg', 'loop-sweep': '45deg' } }
    ],
    layout: { name: 'breadthfirst', directed: true, roots: layoutRoot != null ? [layoutRoot] : undefined, padding: 16, spacingFactor: 1.1 }
  })
  // breadthfirst spaces the pseudo-start node the same one full "level"
  // away from the real start state as any other parent/child pair — this
  // halves that distance after the fact (a non-animated layout has
  // already settled every position by the time the constructor above
  // returns), so the init arrow itself reads shorter than a normal edge,
  // not just differently colored/solid.
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
  applyNextActionHighlight()
  applyFiredActionHighlight()
  syncSelectionToSelection()
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
  if (props.nextActionEdge) cyGraph.edges().filter(e => e.data('matchStateKey') === props.nextActionEdge.stateKey && e.data('actionName') === props.nextActionEdge.actionName).addClass('next-action')
}

function applyFiredActionHighlight() {
  if (!cyGraph) return
  cyGraph.edges().removeClass('fired-action')
  if (props.firedActionEdge) cyGraph.edges().filter(e => e.data('matchStateKey') === props.firedActionEdge.stateKey && e.data('actionName') === props.firedActionEdge.actionName).addClass('fired-action')
}

// A selected transition should open the *action* that fired it, not the
// state it landed on — firedActionEdge (set only while a transition is
// selected, see EditProjectView.vue/BenchmarkProjectView.vue's own
// firedActionEdge computed) takes priority over highlightedStateKey here.
// Reads the edge straight off cyGraph (already carrying the same
// PSEUDO_START_ID/isInitEdge-normalized shape handleEdgeTap hands to a
// real click) rather than re-deriving it from the raw edge list, so a
// programmatic selection looks identical to a manual one.
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
  // No live state to auto-follow at all (edit mode has none, ever — see
  // EditProjectView.vue's own highlightedStateKey) is not the same thing
  // as "the previously highlighted one went away" (a real, still-worth-
  // clearing case, just below) — this runs after *every* graph reload
  // (see renderGraph), not just an actual highlightedStateKey change, so
  // treating "nothing to follow" as "clear the selection" would silently
  // wipe out whatever the user (or a field edit's own re-selection, see
  // EditProjectView.vue's own handleSetStateField) had independently and
  // deliberately selected, on every single reload.
  if (key == null) return
  const node = graphNodes.value.find((n) => n.state.key === key)
  if (!node) return selectGraphElement(null, null)
  selectGraphElement('state', nodeToCyData(node))
  if (emitJump) emit('jump-to-definition', { kind: 'state', stateKey: node.state.key })
}

function resize() { cyGraph?.resize() }
function fit() { cyGraph?.fit() }

// Reloads the graph's own data unconditionally (matches this tab's
// pre-slot-refactor behavior — see Inspector.vue's old plain refresh(),
// which always reloaded Graph regardless of which tab was open, since
// other Inspector tabs' own highlighting depends on current graph data
// too) — `active` only gates the resize+fit a becoming-visible tab needs
// to lay out correctly (a v-show'd cytoscape container has no real
// dimensions to measure until it's actually displayed).
async function refresh(active) {
  await loadGraph()
  if (active) {
    await nextTick()
    cyGraph?.resize()
    cyGraph?.fit()
  }
}

watch(() => props.highlightedStateKey, () => { applyCurrentStateHighlight(); syncSelectionToSelection({ emitJump: props.autoJumpOnHighlightChange }) })
watch(() => props.nextActionEdge, applyNextActionHighlight, { deep: true })
watch(() => props.firedActionEdge, () => { applyFiredActionHighlight(); syncSelectionToSelection({ emitJump: props.autoJumpOnHighlightChange }) }, { deep: true })

// Lookups for the Inspector's own "State"/"Actions" tabs (see
// EditProjectView.vue's own inspectorTabs, shown instead of this
// component while editorOpen is on) — resolving the *shared* selection
// (this component's own emitted 'select', lifted up to EditProjectView.
// vue) back into the same {kind, data} shape a direct graph click would
// have produced, off the exact same already-loaded graphNodes/graphEdges
// rather than a second, possibly-inconsistent fetch of their own.
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
/* An imported session (see the imported prop's own docstring) has no
   avance-computed state to compare against — same green as -correct
   above, under its own class name so "labelled" and "verified correct"
   stay distinct in the markup even though they read the same visually. */
.inspector-annotation-select-labelled { border-color: #2e7d32; background: #e8f5e9; color: #333; }
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
</style>
