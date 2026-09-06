<script setup>
// The design panel's own view for a selected `avance:env` source (see
// FileExplorer.vue's "Sources" branch / ProjectDesignPanel.vue's own
// currentSourceIsEnv) — this driver has no archive file of its own
// (tracking.sources.avance_env), so there's nothing for SourceContentPanel's
// CSV editor to open; this shows the automaton's own exported env keys
// instead, read-only (ai-access/ai-definition are edited from the Env
// keys tab, never from here — see the "Open Env keys" link below).
import { onMounted, ref, watch } from 'vue'
import { getProjectEnvKeys } from '../../../../api.js'

const props = defineProps({
  projectId: { type: String, required: true },
})

const emit = defineEmits(['switch-to-env-keys'])

const loading = ref(true)
const exportedKeys = ref([])

async function load() {
  loading.value = true
  try {
    const { env_keys } = await getProjectEnvKeys(props.projectId)
    exportedKeys.value = env_keys
      .map((entry) => entry.env_key)
      .filter((envKey) => envKey.ai_access && envKey.ai_access !== 'none')
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.projectId, load)
</script>

<template>
  <div class="automaton-variables-panel">
    <div class="automaton-variables-panel-header">
      <h3 class="automaton-variables-panel-title">Automaton variables</h3>
      <p class="automaton-variables-panel-subtitle">
        The env keys this source exposes to the model — read-only here.
        <button type="button" class="automaton-variables-panel-link" @click="emit('switch-to-env-keys')">
          Edit in the Env keys panel
        </button>
      </p>
    </div>
    <div class="automaton-variables-panel-body">
      <p v-if="loading" class="automaton-variables-panel-empty">Loading…</p>
      <p v-else-if="!exportedKeys.length" class="automaton-variables-panel-empty">
        No env key has <code>ai-access: readonly</code> or <code>ai-access: readwrite</code> yet — this source
        exposes nothing until one does.
      </p>
      <ul v-else class="automaton-variables-panel-list">
        <li v-for="envKey in exportedKeys" :key="envKey.name" class="automaton-variables-panel-item">
          <div class="automaton-variables-panel-item-header">
            <span class="automaton-variables-panel-item-name">{{ envKey.name }}</span>
            <span
              class="automaton-variables-panel-badge"
              :class="`automaton-variables-panel-badge-${envKey.ai_access}`"
            >{{ envKey.ai_access }}</span>
          </div>
          <p v-if="envKey.ai_definition" class="automaton-variables-panel-item-definition">{{ envKey.ai_definition }}</p>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.automaton-variables-panel { flex: 1; display: flex; flex-direction: column; min-height: 0; font-size: 0.85rem; color: #333; }
.automaton-variables-panel-header { padding: 0.75rem 1rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.automaton-variables-panel-title { margin: 0 0 0.25rem; font-size: 0.95rem; }
.automaton-variables-panel-subtitle { margin: 0; font-size: 0.78rem; color: #777; }
.automaton-variables-panel-link {
  margin-left: 0.35rem;
  padding: 0;
  border: none;
  background: none;
  color: #3949ab;
  text-decoration: underline;
  cursor: pointer;
  font: inherit;
  font-size: 0.78rem;
}
.automaton-variables-panel-body { flex: 1; overflow-y: auto; padding: 0.75rem 1rem; }
.automaton-variables-panel-empty { color: #888; font-size: 0.82rem; }
.automaton-variables-panel-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.6rem; }
.automaton-variables-panel-item { border: 1px solid #eee; border-radius: 8px; padding: 0.5rem 0.6rem; background: #fafafa; }
.automaton-variables-panel-item-header { display: flex; align-items: center; gap: 0.5rem; }
.automaton-variables-panel-item-name { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-weight: 600; }
.automaton-variables-panel-badge { flex-shrink: 0; padding: 0.05rem 0.4rem; border-radius: 999px; font-size: 0.65rem; font-weight: 600; text-transform: uppercase; }
.automaton-variables-panel-badge-readonly { background: #e8eef7; color: #3355aa; }
.automaton-variables-panel-badge-readwrite { background: #e6f4ea; color: #1e7d34; }
.automaton-variables-panel-item-definition { margin: 0.35rem 0 0; color: #555; font-size: 0.78rem; line-height: 1.4; }
</style>
