<script setup>
import { inject, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { TabulatorFull as Tabulator } from 'tabulator-tables'
import 'tabulator-tables/dist/css/tabulator.min.css'
import { parseCsvRows } from '../../csvTable.js'

const props = defineProps({
  title: { type: String, required: true },
  hint: { type: String, default: '' },
  emptyLabel: { type: String, default: 'Nothing to show yet.' },
  reader: { type: Object, required: true }
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
    content = await props.reader.read()
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
  <div class="source-csv-dialog">
    <h2 class="source-csv-dialog-title">{{ title }}</h2>
    <p v-if="hint" class="source-csv-dialog-hint">{{ hint }}</p>
    <p v-if="loading" class="source-csv-dialog-status">Loading…</p>
    <p v-else-if="!hasRows" class="source-csv-dialog-status">{{ emptyLabel }}</p>
    <div v-show="!loading && hasRows" ref="tableHost" class="source-csv-dialog-table"></div>
    <div class="source-csv-dialog-actions">
      <button type="button" class="source-csv-dialog-ok-btn" @click="closeDialog()">Close</button>
    </div>
  </div>
</template>

<style scoped>
.source-csv-dialog { display: flex; flex-direction: column; min-height: 0; max-height: 70vh; }
.source-csv-dialog-title { margin: 0 0 0.3rem; font-size: 1.05rem; }
.source-csv-dialog-hint { margin: 0 0 0.75rem; font-size: 0.85rem; color: #666; }
.source-csv-dialog-status { margin: 0; padding: 1rem 0; color: #444; }
.source-csv-dialog-table { flex: 1; min-height: 12rem; overflow: auto; }
.source-csv-dialog-actions { display: flex; justify-content: flex-end; margin-top: 0.75rem; }
.source-csv-dialog-ok-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #ccc; background: white; cursor: pointer; }
.source-csv-dialog-ok-btn:hover { background: #f0f0f0; }
</style>
