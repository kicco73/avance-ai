<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getModels } from '../api.js'

const DEFAULT_MODEL_NAME = 'default'

const emit = defineEmits(['select', 'upload', 'download', 'delete'])

const open = ref(false)
const loading = ref(false)
const error = ref('')
const models = ref([])
const activeModelName = ref(null)
const rootEl = ref(null)

const deleteDisabled = computed(() => activeModelName.value === DEFAULT_MODEL_NAME)

// The single fetch behind both the menu's tick and the button's own label —
// called on mount (so the button already shows the right name before the
// menu is ever opened), whenever the dropdown opens, and imperatively via
// `refresh()` (exposed below) after the parent completes a switch/upload/
// delete — never a second, separate call just to relabel the button.
async function loadModels() {
  loading.value = true
  error.value = ''
  try {
    const res = await getModels()
    models.value = res.models
    activeModelName.value = res.active
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

async function toggle() {
  if (open.value) {
    open.value = false
    return
  }
  open.value = true
  await loadModels()
}

onMounted(loadModels)

defineExpose({ refresh: loadModels })

function selectModel(name) {
  open.value = false
  emit('select', name)
}

function selectUpload() {
  open.value = false
  emit('upload')
}

function selectDownload() {
  if (!activeModelName.value) return
  open.value = false
  emit('download', activeModelName.value)
}

// Destructive and irreversible, so confirm via the browser's own dialog
// before emitting — no custom confirm UI to keep in sync.
function selectDelete() {
  if (deleteDisabled.value || !activeModelName.value) return
  const name = activeModelName.value
  if (!window.confirm(`Delete model "${name}"? This cannot be undone.`)) return
  open.value = false
  emit('delete', name)
}

// Closing on outside click, matching a standard dropdown affordance — the
// panel otherwise has no other way to dismiss itself.
function handleClickOutside(event) {
  if (open.value && rootEl.value && !rootEl.value.contains(event.target)) {
    open.value = false
  }
}
document.addEventListener('click', handleClickOutside, true)
onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside, true)
})
</script>

<template>
  <div class="models-menu" ref="rootEl">
    <button class="models-btn" :title="activeModelName ?? 'Models'" @click="toggle">
      {{ activeModelName ?? 'Models' }}
    </button>

    <div v-if="open" class="models-panel">
      <p v-if="loading" class="models-status">Loading…</p>
      <p v-else-if="error" class="models-status models-error">{{ error }}</p>

      <ul v-else class="models-list">
        <li v-for="name in models" :key="name">
          <button class="models-item" @click="selectModel(name)">
            <span class="models-item-check">{{ name === activeModelName ? '✓' : '' }}</span>
            {{ name }}
          </button>
        </li>
        <li>
          <button class="models-item models-upload-item" @click="selectUpload">Upload...</button>
        </li>
        <li>
          <button class="models-item models-download-item" @click="selectDownload">Download</button>
        </li>
        <li>
          <button
            class="models-item models-delete-item"
            :disabled="deleteDisabled"
            @click="selectDelete"
          >
            Delete
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.models-menu {
  position: relative;
}

.models-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  max-width: 160px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.models-btn:hover {
  background: #4a6fa5;
  color: white;
}

.models-panel {
  position: absolute;
  top: calc(100% + 0.4rem);
  right: 0;
  min-width: 180px;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 100;
  overflow: hidden;
}

.models-status {
  margin: 0;
  padding: 0.6rem 0.9rem;
  font-size: 0.85rem;
  color: #444;
}

.models-error {
  color: #c62828;
}

.models-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
}

.models-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.9rem;
  color: #333;
}

.models-item:hover:not(:disabled) {
  background: #f0f4fa;
}

.models-item-check {
  display: inline-block;
  width: 1.1rem;
  color: #2e7d32;
  font-weight: 600;
}

.models-upload-item {
  border-top: 1px solid #eee;
  color: #4a6fa5;
}

.models-download-item {
  color: #4a6fa5;
}

.models-delete-item {
  color: #c62828;
}

.models-delete-item:disabled {
  color: #ccc;
  cursor: not-allowed;
}
</style>
