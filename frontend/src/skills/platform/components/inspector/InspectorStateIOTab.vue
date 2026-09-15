<script setup>
import { computed, ref, onMounted } from 'vue'
import { getProjectEnvKeys } from '../../api.js'
import InspectorEnvKeysList from './InspectorEnvKeysList.vue'
import SegmentedControl from '../../../../components/skillkit/SegmentedControl.vue'

const props = defineProps({
  projectId: { type: String, required: true },
  stateKey: { type: String, default: null },
  stateData: { type: Object, default: null },
  recentlyAddedKey: { type: String, default: null },
  saveField: { type: Function, required: true },
})

const emit = defineEmits([
  'set-field', 'jump-to-definition', 'add-env-key', 'set-env-key-field', 'delete-env-key',
])

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

onMounted(refresh)

const AI_MEMORY_STRATEGIES = [
  { id: 'keep', label: 'Keep', title: 'The notes the model has collected so far stay when a transition lands here' },
  { id: 'clear', label: 'Clear', title: 'The model\'s memory is wiped when a transition lands here; it collects afresh until it leaves' }
]

const IO_FIELDS = [
  { name: 'input', label: 'Input', caption: 'What this state reads', badgeClass: 'inspector-detail-badge-env' },
  { name: 'output', label: 'Output', caption: 'What this state produces', badgeClass: 'inspector-detail-badge-output' },
]

const inputNames = computed(() => new Set(props.stateData?.input || []))
const outputNames = computed(() => new Set(props.stateData?.output || []))

function isChecked(field, name) {
  return (field === 'input' ? inputNames.value : outputNames.value).has(name)
}

async function toggle(field, name, event) {
  const current = field === 'input' ? props.stateData?.input || [] : props.stateData?.output || []
  const next = current.includes(name) ? current.filter((n) => n !== name) : [...current, name]
  await props.saveField(field, next)
  event.target.checked = isChecked(field, name)
}

function isChoice(key) {
  return key.type === 'choice'
}

function jumpToEnvKey(name) {
  emit('jump-to-definition', { kind: 'env-key', envKeyName: name })
}
</script>

<template>
  <div class="inspector-signals-section">
    <template v-if="stateKey != null">
      <div class="inspector-io-strategy">
        <span class="inspector-io-strategy-label">AI memory</span>
        <SegmentedControl
          :model-value="stateData?.aiMemoryStrategy ?? 'keep'"
          :options="AI_MEMORY_STRATEGIES"
          @update:model-value="(strategy) => saveField('ai-memory-strategy', strategy)"
        />
      </div>
      <p v-if="envKeysLoading" class="signals-status">Loading…</p>
      <p v-else-if="!envKeys.length" class="signals-status">No env keys declared yet — declare one with nothing selected.</p>
      <template v-else>
        <div v-for="field in IO_FIELDS" :key="field.name" class="inspector-io-block">
          <div class="inspector-signal-header">
            <span class="inspector-detail-badge" :class="field.badgeClass">{{ field.label }}</span>
            <span class="inspector-signal-name">{{ field.caption }}</span>
          </div>
          <label
            v-for="key in envKeys"
            :key="`${field.name}-${key.name}`"
            class="inspector-io-row"
            :class="{ 'inspector-io-row-undefined': !key.ai_definition || isChoice(key) }"
            @click="jumpToEnvKey(key.name)"
          >
            <input
              type="checkbox"
              :checked="isChecked(field.name, key.name)"
              :disabled="!key.ai_definition || isChoice(key)"
              @click.stop
              @change="toggle(field.name, key.name, $event)"
            />
            <span class="inspector-io-name">{{ key.name }}</span>
            <span v-if="isChoice(key)" class="inspector-io-undefined">choice keys are never rendered to the model</span>
            <span v-else-if="key.ai_definition" class="inspector-io-definition">{{ key.ai_definition }}</span>
            <span v-else class="inspector-io-undefined">needs an AI definition — click to write one</span>
          </label>
        </div>
      </template>
    </template>

    <template v-else>
      <div class="inspector-io-section-header">
        <span class="inspector-detail-badge inspector-detail-badge-env">Env</span>
        <span class="inspector-signal-name">The variables this project declares</span>
      </div>
      <InspectorEnvKeysList
        :env-keys="envKeys"
        :loading="envKeysLoading"
        :recently-added-key="recentlyAddedKey"
        @add-env-key="emit('add-env-key')"
        @set-field="(name, field, value) => emit('set-env-key-field', name, field, value)"
        @delete="(name) => emit('delete-env-key', name)"
        @jump-to-definition="(target) => emit('jump-to-definition', target)"
      />
    </template>
  </div>
</template>

<style scoped>
.inspector-signals-section { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 0.8rem; }
.signals-status { margin: 0; color: #444; font-size: 0.9rem; }
.inspector-io-strategy { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; flex-shrink: 0; }
.inspector-io-strategy-label { font-size: 0.78rem; color: #555; }
.inspector-io-block { display: flex; flex-direction: column; gap: 0.3rem; padding: 0.6rem 0.75rem; border-radius: 8px; border: 1px solid #eee; background: #fafafa; }
.inspector-signal-header { display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.2rem; }
.inspector-io-section-header { display: flex; align-items: center; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-env { background: #00838f; }
.inspector-detail-badge-output { background: #004d40; }
.inspector-signal-name { font-weight: 600; font-size: 0.85rem; color: #333; }
.inspector-io-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.25rem 0.1rem; cursor: pointer; border-radius: 4px; }
.inspector-io-row:hover { background: #f0f4fa; }
.inspector-io-name { flex-shrink: 0; font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace; font-size: 0.8rem; color: #333; }
.inspector-io-definition { flex: 1; min-width: 0; font-size: 0.76rem; color: #777; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inspector-io-undefined { flex: 1; min-width: 0; font-size: 0.76rem; font-style: italic; color: #b06a00; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inspector-io-row-undefined .inspector-io-name { color: #999; }
</style>
