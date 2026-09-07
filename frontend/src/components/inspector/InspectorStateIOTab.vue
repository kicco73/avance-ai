<script setup>
// State-level Input/Output tab: input/output don't declare their own
// variables — they each just select a subset of this project's own
// already-declared `env:` variables (see automaton.State.input/output).
// No metadata is duplicated here; a variable's own description/
// ai-definition lives only on its env-key card (InspectorEnvKeysTab.vue).
import { computed, ref, watch, onMounted } from 'vue'
import { getProjectEnvKeys } from '../../api.js'

const props = defineProps({
  projectId: { type: String, required: true },
  stateKey: { type: String, required: true },
  stateData: { type: Object, default: null },
})

const emit = defineEmits(['set-field', 'jump-to-definition'])

const envKeysLoading = ref(true)
const envKeys = ref([])

async function loadEnvKeys() {
  envKeysLoading.value = true
  try {
    envKeys.value = (await getProjectEnvKeys(props.projectId)).env_keys.map((e) => e.env_key)
  } catch {} finally { envKeysLoading.value = false }
}

async function refresh() {
  await loadEnvKeys()
}

defineExpose({ loadEnvKeys, refresh })

onMounted(loadEnvKeys)

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
</script>

<template>
  <div class="inspector-signals-section">
    <p v-if="envKeysLoading" class="signals-status">Loading…</p>
    <p v-else-if="!envKeys.length" class="signals-status">
      No env keys declared yet — declare one in the Env tab first.
    </p>
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
.inspector-signal-name { font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-io-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.25rem 0.1rem; cursor: pointer; border-radius: 4px; }
.inspector-io-row:hover { background: #f0f4fa; }
.inspector-io-name { flex-shrink: 0; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; font-size: 0.8rem; color: #333; }
.inspector-io-definition { flex: 1; min-width: 0; font-size: 0.76rem; color: #777; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
