<script setup>
import { inject, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { TabulatorFull as Tabulator } from 'tabulator-tables'
import 'tabulator-tables/dist/css/tabulator.min.css'
import { getWebSearchCache } from '../../api.js'
import { parseCsvRows } from '../../csvTable.js'

const props = defineProps({
  sessionId: { type: [Number, String], required: true }
})

const closeDialog = inject('closeDialog')

const loading = ref(true)
const hasRows = ref(false)
const tableHost = ref(null)

let table = null

function columnsOf(fields) {
  return fields.map((field) => ({ title: field, field }))
}

onMounted(async () => {
  let content = ''
  try {
    content = (await getWebSearchCache(props.sessionId))?.content ?? ''
  } catch {
  }
  const { fields, data } = parseCsvRows(content)
  hasRows.value = data.length > 0
  loading.value = false
  await nextTick()
  if (!hasRows.value) return
  table = new Tabulator(tableHost.value, {
    data,
    columns: columnsOf(fields),
    layout: 'fitDataStretch',
    height: '100%',
    reactiveData: false
  })
})

onBeforeUnmount(() => {
  table?.destroy()
  table = null
})
</script>

<template>
  <div class="websearch-cache-dialog">
    <h2 class="websearch-cache-dialog-title">Websearch cache</h2>
    <p class="websearch-cache-dialog-hint">
      What <code>task.websearch(…)</code> last found for this session — read-only, wiped when the session closes.
    </p>
    <p v-if="loading" class="websearch-cache-dialog-status">Loading…</p>
    <p v-else-if="!hasRows" class="websearch-cache-dialog-status">Nothing has been searched yet this session.</p>
    <div v-show="!loading && hasRows" ref="tableHost" class="websearch-cache-dialog-table"></div>
    <div class="websearch-cache-dialog-actions">
      <button type="button" class="websearch-cache-dialog-ok-btn" @click="closeDialog()">Close</button>
    </div>
  </div>
</template>

<style scoped>
.websearch-cache-dialog { display: flex; flex-direction: column; min-height: 0; max-height: 70vh; }
.websearch-cache-dialog-title { margin: 0 0 0.3rem; font-size: 1.05rem; }
.websearch-cache-dialog-hint { margin: 0 0 0.75rem; font-size: 0.85rem; color: #666; }
.websearch-cache-dialog-hint code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.82rem; }
.websearch-cache-dialog-status { margin: 0; padding: 1rem 0; color: #444; }
.websearch-cache-dialog-table { flex: 1; min-height: 12rem; overflow: auto; }
.websearch-cache-dialog-actions { display: flex; justify-content: flex-end; margin-top: 0.75rem; }
.websearch-cache-dialog-ok-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; }
.websearch-cache-dialog-ok-btn:hover { background: #f0f0f0; }
</style>
