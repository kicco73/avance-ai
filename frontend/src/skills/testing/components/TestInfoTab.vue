<script setup>
import { computed } from 'vue'
import TestSessionDetail from './TestSessionDetail.vue'
import InspectorSignalDetailCard from './InspectorSignalDetailCard.vue'
import { useStateTabTokens } from '../../../composables/useStateTabTokens.js'
import { totalTokenBudgetPerSession } from '../../../chatStore.js'
import { selection } from '../selection.js'

const props = defineProps({
  workspace: { type: Object, required: true }
})

const { stateTabTokens } = useStateTabTokens(props.workspace.projectId, selection.stateKey)
const showsSignal = computed(() => selection.signalName.value != null)

defineExpose({ refresh() {}, resize() {} })
</script>

<template>
  <InspectorSignalDetailCard v-if="showsSignal" :signal="selection.signal.value" />
  <TestSessionDetail
    v-else
    :selected-element="selection.element.value"
    :state-tokens="stateTabTokens"
    :available-states="workspace.availableStates"
    :selected-session="selection.session.value"
    :session-input-tokens="selection.sessionInputTokens.value"
    :total-token-budget-per-session="totalTokenBudgetPerSession"
    :session-start-element="selection.sessionStartElement.value"
    :session-end-element="selection.sessionEndElement.value"
    :editable-files="workspace.files"
    :highlighted-state-key="workspace.highlightedStateKey"
    @select="workspace.selectElement"
    @select-attachment="workspace.selectAttachment"
    @jump-to-attachment="workspace.jumpToAttachment"
  />
</template>
