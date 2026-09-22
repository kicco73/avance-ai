<script setup>
import { computed } from 'vue'
import { buildEmbedUrl } from '../../../embedParams.js'

const props = defineProps({
  projectId: { type: String, required: true },
  sessionId: { type: [String, Number], required: true }
})

const iframeSrc = computed(() => buildEmbedUrl('preview-chat', props.projectId, props.sessionId))
</script>

<template>
  <iframe :src="iframeSrc" class="preview-chat-iframe" title="Preview chat"></iframe>
</template>

<style scoped>
.preview-chat-iframe {
  flex: 1;
  min-height: 0;
  width: 100%;
  border: none;
}
</style>
