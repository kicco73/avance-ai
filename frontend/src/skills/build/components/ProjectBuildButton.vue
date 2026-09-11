<script setup>
import { computed } from 'vue'

const props = defineProps({
  projectId: { type: String, required: true },
  publishedRevision: { type: Number, default: null },
  revision: { type: Number, default: null }
})

const emit = defineEmits(['activate'])

const blockedReason = computed(() => {
  if (props.publishedRevision === null) return 'This project has never been published.'
  if (props.revision !== null && props.revision !== props.publishedRevision) {
    return `Draft revision ${props.revision} is not published yet — publish it first.`
  }
  return ''
})
</script>

<template>
  <button
    type="button"
    class="project-detail-secondary-btn"
    :disabled="!!blockedReason"
    :title="blockedReason || 'Compile the published revision'"
    @click="emit('activate', projectId)"
  >Build</button>
</template>
