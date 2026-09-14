<script setup>
import { inject, ref } from 'vue'
import { identifierRegistry } from '../../../../identifierRegistry.js'

const props = defineProps({
  sources: { type: Array, required: true },
  stateData: { type: Object, required: true },
  onSetField: { type: Function, required: true }
})

const ACCESS_LEVELS = [
  { id: 'none', label: 'No access', title: 'The model never sees it in this state' },
  { id: 'may', label: 'May read', title: 'The model may read it, at its own choice' },
  { id: 'must', label: 'Must read', title: 'Forced: read once per entry into this state' },
  { id: 'write', label: 'May write', title: 'The model may update it while replying' }
]

const ACCESS_FIELDS = {
  may: 'ai-may-read-sources',
  must: 'ai-must-read-sources',
  write: 'ai-may-write-sources'
}

const may = ref([...(props.stateData.aiMayReadSources || [])])
const must = ref([...(props.stateData.aiMustReadSources || [])])
const write = ref([...(props.stateData.aiMayWriteSources || [])])

const closeDialog = inject('closeDialog')

function levelsFor(name) {
  return ACCESS_LEVELS.filter((level) => level.id !== 'write' || 'update' in (identifierRegistry.value[`source.${name}`] ?? {}))
}

function levelOf(name) {
  if (must.value.includes(name)) return 'must'
  if (write.value.includes(name)) return 'write'
  if (may.value.includes(name)) return 'may'
  return 'none'
}

function setLevel(name, level) {
  Object.entries({ may, must, write }).forEach(([key, list]) => {
    const current = list.value
    const next = key === level ? [...current.filter((n) => n !== name), name] : current.filter((n) => n !== name)
    if (next.length === current.length && next.every((n, i) => n === current[i])) return
    list.value = next
    props.onSetField(ACCESS_FIELDS[key], next)
  })
}
</script>

<template>
  <div class="sources-edit-dialog">
    <h2 class="sources-edit-dialog-title">Sources</h2>
    <p class="sources-edit-dialog-hint">What this state may access.</p>
    <p v-if="!sources.length" class="sources-edit-dialog-status">
      No sources declared yet — declare one in the Sources branch first.
    </p>
    <div
      v-for="src in sources"
      :key="src.name"
      class="inspector-source-row"
      :title="src.ai_definition"
    >
      <span class="inspector-source-name">{{ src.name }}</span>
      <div class="inspector-source-levels">
        <button
          v-for="level in levelsFor(src.name)"
          :key="level.id"
          type="button"
          class="inspector-source-level"
          :class="{ 'inspector-source-level-active': levelOf(src.name) === level.id }"
          :title="level.title"
          @click="setLevel(src.name, level.id)"
        >{{ level.label }}</button>
      </div>
    </div>
    <div class="sources-edit-dialog-actions">
      <button type="button" class="sources-edit-dialog-ok-btn" @click="closeDialog()">Done</button>
    </div>
  </div>
</template>

<style scoped>
.sources-edit-dialog { width: 100%; }
.sources-edit-dialog-title { margin: 0 0 0.3rem; padding-right: 1.6rem; font-size: 1.05rem; font-weight: 600; color: #333; }
.sources-edit-dialog-hint { margin: 0 0 0.8rem; font-size: 0.8rem; color: #777; }
.sources-edit-dialog-status { margin: 0; font-size: 0.85rem; color: #444; }
.inspector-source-row { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; margin-top: 0.45rem; }
.inspector-source-name { font-size: 0.85rem; color: #444; }
.inspector-source-levels { display: flex; border: 1px solid #ddd; border-radius: 999px; overflow: hidden; background: #fff; }
.inspector-source-level { border: none; background: transparent; padding: 0.2rem 0.6rem; font: inherit; font-size: 0.7rem; color: #777; cursor: pointer; }
.inspector-source-level:hover { background: #f2f2f4; }
.inspector-source-level-active { background: #6a1b9a; color: #fff; }
.inspector-source-level-active:hover { background: #6a1b9a; }
.sources-edit-dialog-actions { display: flex; justify-content: flex-end; margin-top: 1.1rem; }
.sources-edit-dialog-ok-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #4a6fa5; background: #4a6fa5; color: white; font-size: 0.85rem; cursor: pointer; }
.sources-edit-dialog-ok-btn:hover { background: #3d5c8a; }
</style>
