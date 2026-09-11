<script setup>
// State-level Input/Output tab: input/output don't declare their own
// variables — they each just select a subset of this project's own
// already-declared `env:` variables (see automaton.State.input/output).
// No metadata is duplicated here; a variable's own description/
// ai-definition lives only on its env-key card (InspectorEnvKeysTab.vue).
import { computed, ref, watch, onMounted } from 'vue'
import { getProjectEnvKeys, getProjectSources } from '../../api.js'
import { identifierRegistry } from '../../../../identifierRegistry.js'

const props = defineProps({
  projectId: { type: String, required: true },
  stateKey: { type: String, required: true },
  stateData: { type: Object, default: null },
})

const emit = defineEmits(['set-field', 'jump-to-definition'])

const envKeysLoading = ref(true)
const envKeys = ref([])
const sourcesLoading = ref(true)
const sources = ref([])

async function loadEnvKeys() {
  envKeysLoading.value = true
  try {
    envKeys.value = (await getProjectEnvKeys(props.projectId)).env_keys.map((e) => e.env_key)
  } catch {} finally { envKeysLoading.value = false }
}

async function loadSources() {
  sourcesLoading.value = true
  try {
    sources.value = (await getProjectSources(props.projectId)).sources.map((s) => s.source)
  } catch {} finally { sourcesLoading.value = false }
}

async function refresh() {
  await Promise.all([loadEnvKeys(), loadSources()])
}

defineExpose({ loadEnvKeys, refresh })

onMounted(refresh)

const inputNames = computed(() => new Set(props.stateData?.input || []))
const outputNames = computed(() => new Set(props.stateData?.output || []))

function isChecked(field, name) {
  return (field === 'input' ? inputNames.value : outputNames.value).has(name)
}

function toggle(field, name) {
  const current = field === 'input' ? props.stateData?.input || [] : props.stateData?.output || []
  const next = current.includes(name) ? current.filter((n) => n !== name) : [...current, name]
  emit('set-field', field, next)
}

function jumpToEnvKey(name) {
  emit('jump-to-definition', { kind: 'env-key', envKeyName: name })
}

// ai-may-read-sources / ai-must-read-sources / ai-may-write-sources: same
// three list fields InspectorDetailCard.vue's badges cycle through, here
// collapsed into one mutually-exclusive choice per source (a source can't
// be both "may read" and "must read" at once — automaton_builder.py's
// _build_state_source_lists already rejects that overlap server-side).
function sourceSupportsWrite(name) {
  return 'update' in (identifierRegistry.value[`source.${name}`] ?? {})
}

function sourceAccess(name) {
  if ((props.stateData?.aiMustReadSources || []).includes(name)) return 'must'
  if ((props.stateData?.aiMayWriteSources || []).includes(name)) return 'write'
  if ((props.stateData?.aiMayReadSources || []).includes(name)) return 'may'
  return 'none'
}

function setSourceAccess(name, level) {
  const may = props.stateData?.aiMayReadSources || []
  const must = props.stateData?.aiMustReadSources || []
  const write = props.stateData?.aiMayWriteSources || []
  const nextMay = level === 'may' ? [...may.filter((n) => n !== name), name] : may.filter((n) => n !== name)
  const nextMust = level === 'must' ? [...must.filter((n) => n !== name), name] : must.filter((n) => n !== name)
  const nextWrite = level === 'write' ? [...write.filter((n) => n !== name), name] : write.filter((n) => n !== name)
  if (nextMay.length !== may.length || !nextMay.every((n, i) => n === may[i])) emit('set-field', 'ai-may-read-sources', nextMay)
  if (nextMust.length !== must.length || !nextMust.every((n, i) => n === must[i])) emit('set-field', 'ai-must-read-sources', nextMust)
  if (nextWrite.length !== write.length || !nextWrite.every((n, i) => n === write[i])) emit('set-field', 'ai-may-write-sources', nextWrite)
}
</script>

<template>
  <div class="inspector-signals-section">
    <p v-if="envKeysLoading" class="signals-status">Loading…</p>
    <template v-else-if="!envKeys.length">
      <p class="signals-status">No env keys declared yet — declare one in the Env tab first.</p>
    </template>
    <template v-else>
      <div class="inspector-io-block">
        <div class="inspector-signal-header">
          <span class="inspector-detail-badge inspector-detail-badge-env">Input</span>
          <span class="inspector-signal-name">What this state reads</span>
        </div>
        <label v-for="key in envKeys" :key="`input-${key.name}`" class="inspector-io-row" @click="jumpToEnvKey(key.name)">
          <input
            type="checkbox"
            :checked="isChecked('input', key.name)"
            @click.stop
            @change="toggle('input', key.name)"
          />
          <span class="inspector-io-name">{{ key.name }}</span>
          <span v-if="key.ai_definition" class="inspector-io-definition">{{ key.ai_definition }}</span>
        </label>
      </div>

      <div class="inspector-io-block">
        <div class="inspector-signal-header">
          <span class="inspector-detail-badge inspector-detail-badge-output">Output</span>
          <span class="inspector-signal-name">What this state produces</span>
        </div>
        <label v-for="key in envKeys" :key="`output-${key.name}`" class="inspector-io-row" @click="jumpToEnvKey(key.name)">
          <input
            type="checkbox"
            :checked="isChecked('output', key.name)"
            @click.stop
            @change="toggle('output', key.name)"
          />
          <span class="inspector-io-name">{{ key.name }}</span>
          <span v-if="key.ai_definition" class="inspector-io-definition">{{ key.ai_definition }}</span>
        </label>
      </div>
    </template>

    <div class="inspector-io-block">
      <div class="inspector-signal-header">
        <span class="inspector-detail-badge inspector-detail-badge-sources">Sources</span>
        <span class="inspector-signal-name">What this state may access</span>
      </div>
      <p v-if="sourcesLoading" class="signals-status">Loading…</p>
      <p v-else-if="!sources.length" class="signals-status">
        No sources declared yet — declare one in the Sources branch first.
      </p>
      <div v-for="src in sources" :key="`source-${src.name}`" class="inspector-io-row inspector-io-row-source">
        <span class="inspector-io-name">{{ src.name }}</span>
        <span v-if="src.ai_definition" class="inspector-io-definition">{{ src.ai_definition }}</span>
        <select
          class="inspector-io-source-select"
          :value="sourceAccess(src.name)"
          @change="setSourceAccess(src.name, $event.target.value)"
        >
          <option value="none">No access</option>
          <option value="may">May read</option>
          <option value="must">Must read</option>
          <option v-if="sourceSupportsWrite(src.name)" value="write">May write</option>
        </select>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inspector-signals-section { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 0.8rem; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-io-block { display: flex; flex-direction: column; gap: 0.3rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.2rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-env { background: #00838f; }
.inspector-detail-badge-output { background: #004d40; }
.inspector-detail-badge-sources { background: #6a1b9a; }
.inspector-signal-name { font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-io-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.25rem 0.1rem; cursor: pointer; border-radius: 4px; }
.inspector-io-row:hover { background: #f0f4fa; }
.inspector-io-row-source { cursor: default; }
.inspector-io-row-source:hover { background: none; }
.inspector-io-name { flex-shrink: 0; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; font-size: 0.8rem; color: #333; }
.inspector-io-definition { flex: 1; min-width: 0; font-size: 0.76rem; color: #777; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inspector-io-source-select { flex-shrink: 0; font-size: 0.76rem; padding: 0.1rem 0.35rem; border-radius: 4px; border: 1px solid #ccc; background: white; color: #333; }
</style>
