<script setup>
// Shows every action of the currently-selected state as a read-only detail
// card, reorderable via drag-and-drop. Never persists itself — a drop only
// emits 'reorder' with {actionName, position}; the caller does the rest.
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import InspectorDetailCard from './InspectorDetailCard.vue'

const props = defineProps({
  actions: { type: Array, default: () => [] }, // [{ kind: 'action', data }]
  editableFiles: { type: Array, default: null },
  selectedElement: { type: Object, default: null },
  firedActionEdge: { type: Object, default: null },
  highlightedStateKey: { type: String, default: null },
  // {key, uiLabel} for each real state; forwarded to each row's target <select>.
  availableStates: { type: Array, default: () => [] },
  // Every declared project env key's name; forwarded to each row's Env editor.
  availableEnvKeys: { type: Array, default: () => [] },
  // False while showing the init-action, which is a singleton the automaton
  // always owns — unlike a real state's actions, it can't have another one
  // added via "+ Add action".
  allowAdd: { type: Boolean, default: true },
  // 'action:<stateKey>/<actionName>' for the row a "+ Add action" click
  // just created; null otherwise.
  recentlyAddedKey: { type: String, default: null }
})

const emit = defineEmits(['select', 'select-attachment', 'reorder', 'set-field', 'delete', 'add-action'])

const draggedIndex = ref(null)
const dragOverIndex = ref(null)
// The row needs draggable="true" for HTML5 drag-and-drop, but that also
// swallows clicks anywhere in it into a drag gesture (breaking the card's
// @click) — so it's only true while the mouse is down on the ⠿ handle.
const dragArmed = ref(false)
// Catches a mouseup that lands outside the handle (drag never armed via
// the handle's own @mouseup), so dragArmed always gets cleared.
function disarm() {
  dragArmed.value = false
}
onMounted(() => window.addEventListener('mouseup', disarm))
onUnmounted(() => window.removeEventListener('mouseup', disarm))

function isSelected(action) {
  return props.selectedElement?.kind === 'action' && props.selectedElement.data.actionName === action.data.actionName
}

// Accordion: at most one row's form open at a time. Cleared when the
// action it points at is removed from the list, so it never references a
// row that no longer exists.
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

// A selection made elsewhere (e.g. a Graph tap) doesn't scroll this tab's
// row into view on its own — that's done in the selectedElement watch
// below once the row exists to scroll to.
const rowRefs = {}
function setRowRef(name, el) {
  if (el) rowRefs[name] = el
  else delete rowRefs[name]
}

// A selection change opens that action's row instead of leaving it closed.
// Only fires on an actual `selectedElement` change, so it doesn't fight the
// row's own click-to-close toggle (see toggleOpen).
watch(
  () => props.selectedElement,
  async (element) => {
    if (element?.kind !== 'action') return
    expandedActionName.value = element.data.actionName
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
  dragArmed.value = false
  if (fromIndex === null || fromIndex === index) return
  const actionName = props.actions[fromIndex]?.data.actionName
  if (actionName == null) return
  emit('reorder', { actionName, position: index })
}

function handleDragEnd() {
  draggedIndex.value = null
  dragOverIndex.value = null
  dragArmed.value = false
}
</script>

<template>
  <div class="inspector-actions-tab">
    <p v-if="!actions.length && !selectedElement" class="inspector-actions-tab-empty">No action selected.</p>
    <p v-else-if="!actions.length" class="inspector-actions-tab-empty">This state has no actions.</p>
    <div
      v-for="(action, index) in actions"
      :key="action.data.actionName"
      :ref="(el) => setRowRef(action.data.actionName, el)"
      class="inspector-actions-tab-row"
      :class="{ 'inspector-actions-tab-row-drag-over': dragOverIndex === index && draggedIndex !== index }"
      :draggable="dragArmed"
      @dragstart="handleDragStart(index)"
      @dragover.prevent="handleDragOver(index)"
      @dragleave="handleDragLeave(index)"
      @drop.prevent="handleDrop(index)"
      @dragend="handleDragEnd"
    >
      <span
        class="inspector-actions-tab-drag-handle"
        title="Drag to reorder"
        @mousedown="dragArmed = true"
        @mouseup="dragArmed = false"
      >⠿</span>
      <InspectorDetailCard
        class="inspector-actions-tab-card"
        :selected-element="action"
        :editable-files="editableFiles"
        :fired-action-edge="firedActionEdge"
        :highlighted-state-key="highlightedStateKey"
        :selectable="!isSelected(action)"
        editable
        :available-states="availableStates"
        :available-env-keys="availableEnvKeys"
        :recently-added-key="recentlyAddedKey"
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
