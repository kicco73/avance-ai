<script setup>
// The Inspector's own "Actions" tab (shown alongside "State" while
// EditProjectView.vue's editorOpen is on — see its own inspectorTabs) —
// every action of the currently-selected state, each as the same
// read-only detail card "States" already shows for one, one per row,
// reorderable via native HTML5 drag-and-drop. Owns none of the actual
// persistence itself: a drop only ever emits 'reorder' with the plain
// {actionName, position} intent — EditProjectView.vue is the one that
// knows the containing state's own key, calls the actual endpoint, and
// coordinates refreshing the graph/code buffer afterward.
import { nextTick, ref, watch } from 'vue'
import InspectorDetailCard from './InspectorDetailCard.vue'

const props = defineProps({
  actions: { type: Array, default: () => [] }, // [{ kind: 'action', data }]
  editableFiles: { type: Array, default: null },
  selectedElement: { type: Object, default: null },
  nextActionEdge: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  highlightedStateKey: { type: String, default: null },
  // Every real state's own {key, uiLabel} — forwarded straight through to
  // each row's own InspectorDetailCard for its target <select>.
  availableStates: { type: Array, default: () => [] },
  // False while this list is showing the init-action (no real state
  // selected — see EditProjectView.vue's own actionsTabList/
  // selectedStateKey) — the init-action is a singleton the automaton
  // itself always owns, not one more action a real state's own "+ Add
  // action" could add another of.
  allowAdd: { type: Boolean, default: true }
})

const emit = defineEmits(['select', 'select-attachment', 'reorder', 'set-field', 'delete', 'add-action'])

const draggedIndex = ref(null)
const dragOverIndex = ref(null)

function isSelected(action) {
  return props.selectedElement?.kind === 'action' && props.selectedElement.data.actionName === action.data.actionName
}

// An accordion — at most one row's own form open at a time (see
// InspectorDetailCard.vue's own `open` prop, now parent-owned for
// exactly this reason). Cleared whenever the action it points at is no
// longer in the list at all (a delete, or a reorder never changes names
// so that's not a concern here) — no stale reference to a row that's
// gone.
const expandedActionName = ref(null)
function toggleOpen(action, isOpen) {
  expandedActionName.value = isOpen ? action.data.actionName : null
}
watch(
  () => props.actions,
  (actions) => {
    if (expandedActionName.value && !actions.some((a) => a.data.actionName === expandedActionName.value)) {
      expandedActionName.value = null
    }
  }
)

// EditProjectView.vue's own edit-mode selection (a Graph tap, a jump from
// elsewhere) never scrolls the page to find this tab's own row on its
// own — the Inspector may already be showing "State" instead of
// "Actions", or the row itself may simply be off-screen further down
// this tab's own scrollable list (see .inspector-actions-tab's own
// overflow-y). Scrolling it into view here is this tab's own
// responsibility once it (and the row) actually exist to scroll to.
const rowRefs = {}
function setRowRef(name, el) {
  if (el) rowRefs[name] = el
  else delete rowRefs[name]
}

watch(
  () => props.selectedElement,
  async (element) => {
    if (element?.kind !== 'action') return
    await nextTick()
    rowRefs[element.data.actionName]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
)

function handleDragStart(index) {
  draggedIndex.value = index
}

function handleDragOver(index) {
  dragOverIndex.value = index
}

function handleDragLeave(index) {
  if (dragOverIndex.value === index) dragOverIndex.value = null
}

function handleDrop(index) {
  const fromIndex = draggedIndex.value
  draggedIndex.value = null
  dragOverIndex.value = null
  if (fromIndex === null || fromIndex === index) return
  const actionName = props.actions[fromIndex]?.data.actionName
  if (actionName == null) return
  emit('reorder', { actionName, position: index })
}

function handleDragEnd() {
  draggedIndex.value = null
  dragOverIndex.value = null
}
</script>

<template>
  <div class="inspector-actions-tab">
    <p v-if="!actions.length" class="inspector-actions-tab-empty">This state has no actions.</p>
    <div
      v-for="(action, index) in actions"
      :key="action.data.actionName"
      :ref="(el) => setRowRef(action.data.actionName, el)"
      class="inspector-actions-tab-row"
      :class="{ 'inspector-actions-tab-row-drag-over': dragOverIndex === index && draggedIndex !== index }"
      draggable="true"
      @dragstart="handleDragStart(index)"
      @dragover.prevent="handleDragOver(index)"
      @dragleave="handleDragLeave(index)"
      @drop.prevent="handleDrop(index)"
      @dragend="handleDragEnd"
    >
      <span class="inspector-actions-tab-drag-handle" title="Drag to reorder">⠿</span>
      <InspectorDetailCard
        class="inspector-actions-tab-card"
        :selected-element="action"
        :editable-files="editableFiles"
        :next-action-edge="nextActionEdge"
        :fired-action-edge="firedActionEdge"
        :highlighted-state-key="highlightedStateKey"
        :selectable="!isSelected(action)"
        editable
        :available-states="availableStates"
        :closable="false"
        :open="expandedActionName === action.data.actionName"
        @update:open="toggleOpen(action, $event)"
        @select="emit('select', action)"
        @select-attachment="emit('select-attachment', $event)"
        @set-field="(field, value) => emit('set-field', action.data.matchStateKey, action.data.actionName, field, value)"
        @delete="emit('delete', action.data.matchStateKey, action.data.actionName)"
      />
    </div>
    <button v-if="allowAdd" class="inspector-actions-tab-add-btn" @click="emit('add-action')">+ Add action</button>
  </div>
</template>

<style scoped>
.inspector-actions-tab { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 0.5rem; }
.inspector-actions-tab-empty { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-actions-tab-row { display: flex; align-items: stretch; gap: 0.3rem; }
.inspector-actions-tab-row-drag-over { outline: 2px dashed #4a6fa5; outline-offset: 2px; border-radius: 8px; }
.inspector-actions-tab-drag-handle { flex-shrink: 0; display: flex; align-items: center; cursor: grab; color: #999; font-size: 0.9rem; padding: 0 0.2rem; user-select: none; }
.inspector-actions-tab-card { flex: 1; min-width: 0; margin-top: 0 !important; }
.inspector-actions-tab-add-btn { flex-shrink: 0; margin-top: 0.5rem; padding: 0.5rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.inspector-actions-tab-add-btn:hover { background: #eef2f9; }
</style>
