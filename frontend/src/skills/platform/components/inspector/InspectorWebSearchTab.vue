<script setup>
import { customDialog } from '../../../../dialogStore.js'
import WebSearchCacheDialog from './WebSearchCacheDialog.vue'

const props = defineProps({
  sessionId: { type: [Number, String], default: null }
})

function openDialog() {
  if (props.sessionId == null) return
  customDialog({ component: WebSearchCacheDialog, props: { sessionId: props.sessionId } })
}
</script>

<template>
  <div class="inspector-websearch-section">
    <button
      type="button"
      class="inspector-detail-card inspector-websearch-card"
      :disabled="sessionId == null"
      @click="openDialog"
    >
      <div class="inspector-detail-header">
        <div class="inspector-detail-header-top">
          <span class="inspector-detail-badge inspector-detail-badge-source">Source</span>
          <span class="inspector-detail-title">Websearch</span>
        </div>
      </div>
      <div class="inspector-detail-body">
        <p>What <code>task.websearch(…)</code> last found for this session — click to view.</p>
      </div>
    </button>
  </div>
</template>

<style scoped>
.inspector-websearch-section { flex: 1; min-height: 0; }
.inspector-websearch-card {
  width: 100%;
  display: flex;
  flex-direction: column;
  border-radius: 8px;
  border: 1px solid #eee;
  background: #fafafa;
  cursor: pointer;
  text-align: left;
  font: inherit;
  padding: 0;
}
.inspector-websearch-card:hover:not(:disabled) { background: #f0f0f0; }
.inspector-websearch-card:disabled { cursor: not-allowed; opacity: 0.6; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-source { background: #3949ab; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-detail-body { padding: 0.6rem 0.75rem; font-size: 0.8rem; color: #444; }
.inspector-detail-body p { margin: 0; }
.inspector-detail-body code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.78rem; }
</style>
