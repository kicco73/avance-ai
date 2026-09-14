<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { TabulatorFull as Tabulator } from 'tabulator-tables'
import 'tabulator-tables/dist/css/tabulator.min.css'
import { getWebSearchCache, deleteWebSearchCache } from '../../../../api.js'
import { parseCsvRows } from '../../../../csvTable.js'

const props = defineProps({
  projectId: { type: String, required: true },
  sourceName: { type: String, required: true }
})

const loading = ref(true)
const clearing = ref(false)
const rowCount = ref(0)
const tableHost = ref(null)

const hasRows = computed(() => rowCount.value > 0)

let table = null
let requestToken = 0

function columnsOf(fields) {
  return fields.map((field) => ({ title: field, field }))
}

async function load() {
  const token = ++requestToken
  loading.value = true
  let content = ''
  try {
    content = (await getWebSearchCache(props.projectId))?.content ?? ''
  } catch {
  }
  if (token !== requestToken) return
  const { fields, data } = parseCsvRows(content)
  rowCount.value = data.length
  loading.value = false
  await nextTick()
  if (token !== requestToken) return
  if (table) {
    table.setColumns(columnsOf(fields))
    table.setData(data)
    return
  }
  table = new Tabulator(tableHost.value, {
    data,
    columns: columnsOf(fields),
    layout: 'fitDataStretch',
    height: '100%',
    reactiveData: false
  })
}

async function clearCache() {
  clearing.value = true
  try {
    await deleteWebSearchCache(props.projectId)
    await load()
  } catch {
  } finally {
    clearing.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => {
  table?.destroy()
  table = null
})
</script>

<template>
  <div class="websearch-source-panel">
    <div class="websearch-source-toolbar">
      <span class="websearch-source-scope">Last <code>task.websearch(…)</code> results, for you</span>
      <button
        class="websearch-source-clear-btn"
        :disabled="clearing || loading || !hasRows"
        title="Delete every row stored for you — the source then reads as empty"
        @click="clearCache"
      >{{ clearing ? 'Clearing…' : 'Clear cache' }}</button>
    </div>

    <p v-if="loading" class="websearch-source-status">Loading…</p>
    <div v-show="!loading && hasRows" ref="tableHost" class="websearch-source-table"></div>
    <div v-if="!loading && !hasRows" class="websearch-source-empty">
      <span class="websearch-source-empty-icon">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor">
          <path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z" />
        </svg>
      </span>
      <p><code>source.{{ sourceName }}</code> reads what <code>task.websearch(…)</code> last found for whoever is talking. Nothing is stored yet, so it reads as an empty table.</p>
    </div>
  </div>
</template>

<style scoped>
.websearch-source-panel { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.websearch-source-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; flex-shrink: 0; }
.websearch-source-scope { min-width: 0; font-size: 0.8rem; color: #777; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.websearch-source-clear-btn { flex-shrink: 0; padding: 0.35rem 0.7rem; border-radius: 6px; border: 1px solid #b3261e; background: white; color: #b3261e; cursor: pointer; font-size: 0.82rem; }
.websearch-source-clear-btn:hover:not(:disabled) { background: #fdeceb; }
.websearch-source-clear-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.websearch-source-status { margin: 0; padding: 1rem; color: #444; }
.websearch-source-table { flex: 1; min-height: 0; overflow: auto; }
.websearch-source-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.4rem; padding: 1rem; text-align: center; color: #777; }
.websearch-source-empty-icon { color: #8b5cf6; opacity: 0.7; }
.websearch-source-empty p { margin: 0; font-size: 0.9rem; max-width: 34rem; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.82rem; color: #444; }
</style>
