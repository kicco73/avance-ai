<script setup>
import { onMounted, ref } from 'vue'
import { getProjectSources } from '../../api.js'
import { inspectorFor } from './sourceInspectors.js'

const props = defineProps({
  projectId: { type: String, required: true },
  sessionId: { type: [Number, String], default: null }
})

const sourcesLoading = ref(true)
const sources = ref([])

async function loadSources() {
  sourcesLoading.value = true
  try {
    sources.value = (await getProjectSources(props.projectId, props.sessionId ?? undefined))
      .sources.map((row) => row.source)
  } catch {
    sources.value = []
  } finally {
    sourcesLoading.value = false
  }
}

function inspectorOf(source) {
  return inspectorFor(source)
}

function openSource(source) {
  inspectorFor(source).open(source, { projectId: props.projectId, sessionId: props.sessionId })
}

defineExpose({ loadSources })

onMounted(loadSources)
</script>

<template>
  <div class="inspector-sources-list">
    <p v-if="sourcesLoading" class="signals-status">Loading…</p>
    <p v-else-if="!sources.length" class="signals-status">This project declares no sources.</p>
    <template v-else>
      <button
        v-for="source in sources"
        :key="source.name"
        type="button"
        class="inspector-detail-card inspector-source-item"
        :disabled="!inspectorOf(source).isReachable({ sessionId })"
        @click="openSource(source)"
      >
        <div class="inspector-detail-header">
          <div class="inspector-detail-header-top">
            <span class="inspector-detail-badge inspector-detail-badge-source">Source</span>
            <span class="inspector-detail-title">{{ source.ui_label || source.name }}</span>
          </div>
        </div>
        <div class="inspector-detail-body">
          <p>{{ inspectorOf(source).caption(source) }}</p>
        </div>
      </button>
    </template>
  </div>
</template>

<style scoped>
.inspector-sources-list { display: flex; flex-direction: column; gap: 0.5rem; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-source-item {
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
.inspector-source-item:hover:not(:disabled) { background: #f0f0f0; }
.inspector-source-item:disabled { cursor: not-allowed; opacity: 0.6; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-source { background: #3949ab; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-detail-body { padding: 0.6rem 0.75rem; font-size: 0.8rem; color: #444; }
.inspector-detail-body p { margin: 0; overflow: hidden; text-overflow: ellipsis; }
</style>
