<script setup>
import { onMounted } from 'vue'
import ProjectTestPanel from './ProjectTestPanel.vue'
import { selection } from '../selection.js'

const props = defineProps({
  workspace: { type: Object, required: true }
})

onMounted(() => selection.open(props.workspace.projectId, props.workspace))
</script>

<template>
  <ProjectTestPanel :project-id="workspace.projectId" @select="selection.handleAutoSelect" />
</template>
