<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import InspectorDetailCard from './InspectorDetailCard.vue'
import { putActionOrder } from '../../api.js'

const props = defineProps({
  projectId: { type: String, required: true },
  stateName: { type: String, required: true },
  actions: { type: Array, default: () => [] }
})

const orderedActions = ref([...props.actions])

const draggedIndex = ref(null)
const dragOverIndex = ref(null)
const dragArmed = ref(false)
function disarm() { dragArmed.value = false }
onMounted(() => window.addEventListener('mouseup', disarm))
onUnmounted(() => window.removeEventListener('mouseup', disarm))

function handleDragStart(index) { draggedIndex.value = index }
function handleDragOver(index) { dragOverIndex.value = index }
function handleDragLeave(index) { if (dragOverIndex.value === index) dragOverIndex.value = null }

function handleDrop(index) {
  const fromIndex = draggedIndex.value
  draggedIndex.value = null
  dragOverIndex.value = null
  dragArmed.value = false
  if (fromIndex === null || fromIndex === index) return
  const [moved] = orderedActions.value.splice(fromIndex, 1)
  orderedActions.value.splice(index, 0, moved)
  putActionOrder(props.projectId, props.stateName, moved.data.actionName, index)
}

function handleDragEnd() {
  draggedIndex.value = null
  dragOverIndex.value = null
  dragArmed.value = false
}
</script>

<template>
  <div class="actions-order-dialog">
    <h2 class="actions-order-title">Actions order</h2>
    <p v-if="!orderedActions.length" class="actions-order-empty">This state has no actions.</p>
    <div v-else class="actions-order-list">
      <div
        v-for="(action, index) in orderedActions"
        :key="action.data.actionName"
        class="actions-order-row"
        :class="{ 'actions-order-row-drag-over': dragOverIndex === index && draggedIndex !== index }"
        :draggable="dragArmed"
        @dragstart="handleDragStart(index)"
        @dragover.prevent="handleDragOver(index)"
        @dragleave="handleDragLeave(index)"
        @drop.prevent="handleDrop(index)"
        @dragend="handleDragEnd"
      >
        <span
          class="actions-order-drag-handle"
          title="Drag to reorder"
          @mousedown="dragArmed = true"
          @mouseup="dragArmed = false"
        >⠿</span>
        <InspectorDetailCard class="actions-order-card" :selected-element="action" :closable="false" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.actions-order-dialog { text-align: left; }
.actions-order-title { margin: 0 0 0.8rem; padding-right: 1.6rem; font-size: 1.05rem; font-weight: 600; color: #333; }
.actions-order-empty { margin: 0; color: #666; font-size: 0.85rem; }
.actions-order-list { display: flex; flex-direction: column; gap: 0.5rem; max-height: min(60vh, 420px); overflow-y: auto; }
.actions-order-row { display: flex; align-items: stretch; gap: 0.3rem; }
.actions-order-row-drag-over { outline: 2px dashed #4a6fa5; outline-offset: 2px; border-radius: 8px; }
.actions-order-drag-handle { flex-shrink: 0; display: flex; align-items: center; cursor: grab; color: #999; font-size: 0.9rem; padding: 0 0.2rem; user-select: none; }
.actions-order-card { flex: 1; min-width: 0; margin-top: 0 !important; }
</style>
