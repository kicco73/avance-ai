<script setup>
import { computed } from 'vue'
import { projectFileTypes } from '../../../../../../projectFileTypes.js'

const props = defineProps({
  fileName: { type: String, required: true },
  contentUrl: { type: String, required: true }
})

const fileType = computed(() => projectFileTypes.value.of(props.fileName))
</script>

<template>
  <div class="aspect-media-panel">
    <div v-if="fileType.isAudio" class="aspect-media-content aspect-media-audio">
      <span class="aspect-media-filename">{{ fileName }}</span>
      <audio :key="contentUrl" controls preload="metadata" :src="contentUrl"></audio>
      <span class="aspect-media-type">{{ fileType.label }}</span>
    </div>
    <div v-else class="aspect-media-content aspect-media-image">
      <img :key="contentUrl" :src="contentUrl" :alt="fileName" />
    </div>
  </div>
</template>

<style scoped>
.aspect-media-panel { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.aspect-media-content { flex: 1; min-height: 0; display: flex; }
.aspect-media-image { align-items: center; justify-content: center; overflow: auto; background: repeating-conic-gradient(#f0f0f0 0% 25%, #fafafa 0% 50%) 50% / 20px 20px; }
.aspect-media-image img { max-width: 100%; max-height: 100%; object-fit: contain; }
.aspect-media-audio { flex-direction: column; align-items: center; justify-content: center; gap: 0.75rem; padding: 1rem; background: #fafafa; }
.aspect-media-audio audio { width: min(100%, 30rem); }
.aspect-media-filename { max-width: 100%; font-size: 0.9rem; font-weight: 600; color: #333; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.aspect-media-type { font-size: 0.8rem; color: #777; }
</style>
