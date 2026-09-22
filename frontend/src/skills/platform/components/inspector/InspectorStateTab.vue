<script setup>
import { computed, ref, watch } from 'vue'
import InspectorDetailCard from '../../../../components/skillkit/InspectorDetailCard.vue'
import InspectorSourceCard from './InspectorSourceCard.vue'
import SourcesEditDialog from './SourcesEditDialog.vue'
import { customDialog } from '../../../../dialogStore.js'
import SessionDetailCard from '../../../../components/skillkit/SessionDetailCard.vue'

const props = defineProps({
  projectId: { type: String, required: true },
  selectedElement: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  highlightedStateKey: { type: String, default: null },
  firedActionEdge: { type: Object, default: null },
  availableStates: { type: Array, default: () => [] },
  saveField: { type: Function, default: null },
  recentlyAddedKey: { type: String, default: null },
  currentFileName: { type: String, default: null },
  sources: { type: Array, default: () => [] },
  selectedSource: { type: Object, default: null },
  deletingSource: { type: String, default: null },
  sourcesRootSelected: { type: Boolean, default: false },
  selectedSession: { type: Object, default: null },
  sessionInputTokens: { type: Number, default: null },
  totalTokenBudgetPerSession: { type: Number, default: null },
  sessionStartElement: { type: Object, default: null },
  sessionEndElement: { type: Object, default: null },
  stateTokens: { type: Number, default: null }
})

const emit = defineEmits([
  'select', 'select-attachment', 'jump-to-attachment', 'set-field', 'delete',
  'add-state', 'add-action', 'open-actions-order',
  'set-source-field', 'delete-source'
])

const isSourceContext = computed(() => props.selectedSource != null)

const isBehaviorContext = computed(() => (
  !isSourceContext.value && !props.sourcesRootSelected && (!props.currentFileName || props.currentFileName === 'index.yml')
))

const sessionStartIsEnd = computed(() => (
  props.sessionStartElement != null && props.sessionStartElement.data.id === props.sessionEndElement?.data.id
))

const elementIdentity = computed(() => {
  const el = props.selectedElement
  if (!el) return null
  return el.kind === 'state' ? `state:${el.data.id}` : `action:${el.data.matchStateKey}/${el.data.actionName}`
})

function openSourcesDialog() {
  customDialog({
    component: SourcesEditDialog,
    props: {
      sources: props.sources.map((entry) => entry.source),
      stateData: props.selectedElement.data,
      onSetField: (field, value) => emit('set-field', field, value)
    }
  })
}

const open = ref(false)
watch(elementIdentity, (identity) => {
  open.value = identity != null && (props.selectedElement.kind === 'state' || props.recentlyAddedKey === identity)
}, { immediate: true })

</script>

<template>
  <div class="inspector-state-tab">
    <InspectorSourceCard
      v-if="isSourceContext"
      :source="selectedSource"
      :deleting="deletingSource === selectedSource?.name"
      @set-field="(field, value) => emit('set-source-field', field, value)"
      @delete="emit('delete-source', selectedSource)"
    />

    <template v-if="selectedSession">
      <SessionDetailCard
        :session="selectedSession"
        :editable="false"
        :session-input-tokens="sessionInputTokens"
        :total-token-budget-per-session="totalTokenBudgetPerSession"
      />
      <InspectorDetailCard
        v-if="sessionStartIsEnd"
        :selected-element="sessionStartElement"
        :closable="false"
        role-badge="Start / End"
      />
      <template v-else>
        <InspectorDetailCard v-if="sessionStartElement" :selected-element="sessionStartElement" :closable="false" role-badge="Start" />
        <InspectorDetailCard v-if="sessionEndElement" :selected-element="sessionEndElement" :closable="false" role-badge="End" />
      </template>
    </template>

    <InspectorDetailCard
      v-else-if="selectedElement && isBehaviorContext"
      :selected-element="selectedElement"
      :state-tokens="stateTokens"
      :editable-files="editableFiles"
      :fired-action-edge="firedActionEdge"
      :highlighted-state-key="highlightedStateKey"
      :available-states="availableStates"
      :recently-added-key="recentlyAddedKey"
      :selectable="true"
      :editable="true"
      :save-field="saveField"
      :closable="false"
      :open="open"
      @update:open="open = $event"
      @select="emit('select', selectedElement)"
      @select-attachment="emit('select-attachment', $event)"
      @jump-to-attachment="emit('jump-to-attachment', $event)"
      @set-field="(field, value) => emit('set-field', field, value)"
      @delete="emit('delete', selectedElement)"
      @open-actions-order="emit('open-actions-order', selectedElement)"
      @open-sources="openSourcesDialog"
    />
    <div v-if="isBehaviorContext && selectedElement?.kind !== 'action'" class="inspector-state-tab-add-row">
      <button v-if="!selectedElement" class="inspector-state-tab-add-btn" @click="emit('add-state')">+ Add state</button>
      <button v-else class="inspector-state-tab-add-btn" @click="emit('add-action')">+ Add action</button>
    </div>
  </div>
</template>

<style scoped>
.inspector-state-tab { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
.inspector-state-tab-add-row { flex-shrink: 0; display: flex; gap: 0.5rem; margin-top: 0.5rem; }
.inspector-state-tab-add-btn { flex: 1; padding: 0.5rem; border-radius: 6px; border: 1px dashed #4a6fa5; background: white; color: #4a6fa5; font-size: 0.82rem; cursor: pointer; }
.inspector-state-tab-add-btn:hover { background: #eef2f9; }
</style>
