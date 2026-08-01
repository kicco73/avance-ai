<script setup>
import { renderMarkdown } from '../../markdown.js'

const props = defineProps({
  activeModel: { type: Object, default: null }
})
</script>

<template>
  <div class="inspector-model-section">
    <p v-if="!activeModel" class="signals-status">No AI model configured.</p>
    <div v-else class="inspector-signal-block">
      <div class="inspector-signal-header">
        <span class="inspector-detail-badge inspector-detail-badge-model">Model</span>
        <span class="inspector-signal-name">{{ activeModel.ui_label }}</span>
      </div>
      <br/>
      <p class="inspector-detail-field"><strong>Driver:</strong> {{ activeModel.driver }}</p>
      <p class="inspector-detail-field"><strong>Model:</strong> {{ activeModel.model }}</p>
      <p v-if="activeModel.url" class="inspector-detail-field"><strong>Url:</strong> {{ activeModel.url }}</p>
      <br/>
      <div v-if="activeModel.ui_description" class="inspector-model-description" v-html="renderMarkdown(activeModel.ui_description)"></div>
    </div>
  </div>
</template>

<style scoped>
.inspector-model-section { flex: 1; min-height: 0; overflow-y: auto; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-signal-block { display: flex; flex-direction: column; gap: 0.2rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-model { background: #2f8f83; }
.inspector-signal-name { font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-detail-field { margin: 0 0 0.4rem; line-height: 1.4; font-size: 0.8rem; color: #444; }
.inspector-model-description { margin: 0 0 0.5rem; line-height: 1.4; font-size: 0.8rem; color: #444; }
.inspector-model-description :deep(p) { margin: 0 0 0.4rem; }
.inspector-model-description :deep(p:last-child) { margin-bottom: 0; }
.inspector-model-description :deep(ul), .inspector-model-description :deep(ol) { margin: 0 0 0.4rem; padding-left: 1.2rem; }
</style>
