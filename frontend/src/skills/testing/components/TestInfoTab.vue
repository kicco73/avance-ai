<script setup>
import { computed } from 'vue'
// IMPORTANT — the one skill -> skill import left in the frontend, and a
// placeholder for a component that does not exist yet. InspectorStateTab
// belongs to the editor: it drags the whole card subtree behind it
// (InspectorDetailCard, CardMenu, ScriptEditDialog) and calls platform's
// own routes, so while it is imported here the benchmark cannot be built
// without an editor and skills/platform/ cannot take its own screens with
// it. `read-only` below is the shape of the answer rather than the answer:
// a flag turning one component into two, where what the benchmark wants is
// a REDUCED VIEW OF ITS OWN — a past session's state and action detail,
// read, never edited. Until that component is written, this line is the
// dependency.
import InspectorStateTab from '../../../components/inspector/InspectorStateTab.vue'
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
  <InspectorStateTab
    v-else
    :project-id="workspace.projectId"
    :selected-element="selection.element.value"
    :state-tokens="stateTabTokens"
    :available-states="workspace.availableStates"
    :selected-session="selection.session.value"
    :session-input-tokens="selection.sessionInputTokens.value"
    :total-token-budget-per-session="totalTokenBudgetPerSession"
    :session-start-element="selection.sessionStartElement.value"
    :session-end-element="selection.sessionEndElement.value"
    :read-only="true"
    :editable-files="workspace.files"
    :highlighted-state-key="workspace.highlightedStateKey"
    :recently-added-key="workspace.recentlyAddedKey"
    @select="workspace.selectElement"
    @select-attachment="workspace.selectAttachment"
    @jump-to-attachment="workspace.jumpToAttachment"
  />
</template>
