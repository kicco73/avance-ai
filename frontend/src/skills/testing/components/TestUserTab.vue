<script setup>
import InspectorUserInfoCard from '../../../components/inspector/InspectorUserInfoCard.vue'
import { selection } from '../selection.js'

defineProps({
  workspace: { type: Object, required: true }
})

defineExpose({ refresh() {}, resize() {} })
</script>

<template>
  <InspectorUserInfoCard :user="selection.user.value" />
</template>
