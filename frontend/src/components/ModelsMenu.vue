<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { getModels } from '../api.js'

const DEFAULT_MODEL_NAME = 'default'

const emit = defineEmits(['select', 'upload', 'delete'])

const open = ref(false)
const loading = ref(false)
const error = ref('')
const models = ref([])
const rootEl = ref(null)
const confirmingDelete = ref(false)

const activeModelName = computed(() => models.value.find((m) => m.active)?.name ?? null)
const deleteDisabled = computed(() => activeModelName.value === DEFAULT_MODEL_NAME)

async function toggle() {
  if (open.value) {
    open.value = false
    return
  }
  open.value = true
  confirmingDelete.value = false
  loading.value = true
  error.value = ''
  try {
    const res = await getModels()
    models.value = res.models
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

function selectModel(name) {
  open.value = false
  emit('select', name)
}

function selectUpload() {
  open.value = false
  emit('upload')
}

// "Delete" doesn't act immediately — it swaps the panel to an inline
// confirm/cancel step first, since removing a model is destructive and
// irreversible.
function selectDelete() {
  if (deleteDisabled.value || !activeModelName.value) return
  confirmingDelete.value = true
}

function cancelDelete() {
  confirmingDelete.value = false
}

function confirmDelete() {
  const name = activeModelName.value
  confirmingDelete.value = false
  open.value = false
  emit('delete', name)
}

// Closing on outside click, matching a standard dropdown affordance — the
// panel otherwise has no other way to dismiss itself.
function handleClickOutside(event) {
  if (open.value && rootEl.value && !rootEl.value.contains(event.target)) {
    open.value = false
    confirmingDelete.value = false
  }
}
document.addEventListener('click', handleClickOutside, true)
onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside, true)
})
</script>

<template>
  <div class="models-menu" ref="rootEl">
    <button class="models-btn" @click="toggle">Models</button>

    <div v-if="open" class="models-panel">
      <p v-if="loading" class="models-status">Loading…</p>
      <p v-else-if="error" class="models-status models-error">{{ error }}</p>

      <div v-else-if="confirmingDelete" class="models-confirm">
        <p class="models-confirm-text">Delete "{{ activeModelName }}"? This cannot be undone.</p>
        <div class="models-confirm-actions">
          <button class="models-confirm-cancel" @click="cancelDelete">Cancel</button>
          <button class="models-confirm-delete" @click="confirmDelete">Delete</button>
        </div>
      </div>

      <ul v-else class="models-list">
        <li v-for="model in models" :key="model.name">
          <button class="models-item" @click="selectModel(model.name)">
            <span class="models-item-check">{{ model.active ? '✓' : '' }}</span>
            {{ model.name }}
          </button>
        </li>
        <li>
          <button class="models-item models-upload-item" @click="selectUpload">Upload...</button>
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

.models-delete-item {
  color: #c62828;
}

.models-delete-item:disabled {
  color: #ccc;
  cursor: not-allowed;
}

.models-confirm {
  padding: 0.75rem 0.9rem;
}

.models-confirm-text {
  margin: 0 0 0.75rem;
  font-size: 0.85rem;
  color: #333;
}

.models-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

.models-confirm-cancel,
.models-confirm-delete {
  padding: 0.35rem 0.8rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 0.85rem;
}

.models-confirm-cancel {
  border: 1px solid #999;
  background: white;
  color: #666;
}

.models-confirm-cancel:hover {
  background: #f0f0f0;
}

.models-confirm-delete {
  border: 1px solid #c62828;
  background: #c62828;
  color: white;
}

.models-confirm-delete:hover {
  background: #a51f1f;
}
</style>
