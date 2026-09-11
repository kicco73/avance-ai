<script setup>
// The benchmark's own Info panel: a past session, or whatever state or
// action is selected in the Test tree, read and never edited.
//
// It composes the same cards the editor's State tab does (they are in
// components/skillkit/ precisely so both can), and that is the whole
// difference from mounting that tab with read-only turned on — which is
// what this file replaces. A test run has no project to configure, no
// file to browse, no source to define and nothing to add, so none of that
// is here to be switched off.
import { computed } from 'vue'
import InspectorDetailCard from '../../../components/skillkit/InspectorDetailCard.vue'
import SessionDetailCard from '../../../components/skillkit/SessionDetailCard.vue'

const props = defineProps({
  selectedElement: { type: Object, default: null },
  stateTokens: { type: Number, default: null },
  availableStates: { type: Array, default: () => [] },
  editableFiles: { type: Array, default: null },
  highlightedStateKey: { type: String, default: null },
  selectedSession: { type: Object, default: null },
  sessionInputTokens: { type: Number, default: null },
  totalTokenBudgetPerSession: { type: Number, default: null },
  sessionStartElement: { type: Object, default: null },
  sessionEndElement: { type: Object, default: null },
})

const emit = defineEmits(['select', 'select-attachment', 'jump-to-attachment'])

// Same session at both ends of the run: one card with a combined badge
// rather than two identical ones.
const sessionStartIsEnd = computed(() => (
  props.sessionStartElement != null && props.sessionStartElement.data.id === props.sessionEndElement?.data.id
))
</script>

<template>
  <div class="test-session-detail">
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
      v-else-if="selectedElement"
      :selected-element="selectedElement"
      :state-tokens="stateTokens"
      :editable-files="editableFiles"
      :highlighted-state-key="highlightedStateKey"
      :available-states="availableStates"
      :closable="false"
      @select="emit('select', selectedElement)"
      @select-attachment="emit('select-attachment', $event)"
      @jump-to-attachment="emit('jump-to-attachment', $event)"
    />
  </div>
</template>

<style scoped>
.test-session-detail { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }
</style>
