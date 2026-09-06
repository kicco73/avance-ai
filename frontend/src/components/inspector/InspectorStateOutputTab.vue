<script setup>
import { computed, onMounted, ref } from 'vue'
import { getProjectMetadata } from '../../api.js'
import CardMenu from './CardMenu.vue'

const props = defineProps({
  projectId: { type: String, required: true },
  stateKey: { type: String, required: true },
  recentlyAddedKey: { type: String, default: null },
})

const emit = defineEmits(['add-output-key', 'set-field', 'delete'])

const outputKeys = ref([])
const expandedName = ref(null)
const editName = ref('')
const editUiLabel = ref('')
const editUiDescription = ref('')
const editAiDefinition = ref('')

async function loadOutputKeys() {
  try {
    const { project } = await getProjectMetadata(props.projectId)
    const state = Object.values(project.states || {}).find(s => s.id === props.stateKey)
    outputKeys.value = state?.output_keys || []
  } catch (e) {
    console.error('Failed to load output keys:', e)
  }
}

function selectOutputKey(name) {
  if (expandedName.value === name) {
    expandedName.value = null
  } else {
    expandedName.value = name
    const key = outputKeys.value.find(k => k.name === name)
    if (key) {
      editName.value = key.name
      editUiLabel.value = key.ui_label || ''
      editUiDescription.value = key.ui_description || ''
      editAiDefinition.value = key.ai_definition || ''
    }
  }
}

function commitField(field, currentValue, originalValue) {
  if (currentValue !== originalValue) {
    emit('set-field', expandedName.value, field, currentValue)
  }
}

async function deleteOutputKey(name) {
  emit('delete', name)
  expandedName.value = null
  await loadOutputKeys()
}

onMounted(loadOutputKeys)
</script>

<template>
  <div class="inspector-output-tab">
    <div v-if="!outputKeys.length" class="empty-state">
      <p>No output fields declared. Add one to allow the LLM to produce structured values this state can use in triggers and actions.</p>
    </div>
    <div v-for="key in outputKeys" :key="key.name" class="inspector-signal-block">
      <div class="block-header" @click="selectOutputKey(key.name)">
        <div class="header-left">
          <span class="block-title">{{ key.name }}</span>
          <span v-if="key.ui_label" class="block-label">{{ key.ui_label }}</span>
        </div>
        <div class="header-right">
          <span class="chevron" :class="{ open: expandedName === key.name }">›</span>
        </div>
      </div>
      <div v-if="expandedName === key.name" class="block-content">
        <div class="field-group">
          <label>Name</label>
          <input
            v-model="editName"
            type="text"
            @blur="commitField('name', editName, key.name)"
          />
        </div>
        <div class="field-group">
          <label>Label</label>
          <input
            v-model="editUiLabel"
            type="text"
            @blur="commitField('ui_label', editUiLabel, key.ui_label || '')"
          />
        </div>
        <div class="field-group">
          <label>Description</label>
          <textarea
            v-model="editUiDescription"
            rows="2"
            @blur="commitField('ui_description', editUiDescription, key.ui_description || '')"
          />
        </div>
        <div class="field-group">
          <label>AI Definition (required)</label>
          <textarea
            v-model="editAiDefinition"
            rows="3"
            @blur="commitField('ai_definition', editAiDefinition, key.ai_definition || '')"
          />
        </div>
        <div class="block-footer">
          <CardMenu
            @delete="deleteOutputKey(key.name)"
          />
        </div>
      </div>
    </div>
    <button class="add-button" @click="emit('add-output-key')">
      + Add output field
    </button>
  </div>
</template>

<style scoped>
.inspector-output-tab {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  padding: 0.5rem;
  gap: 0.5rem;
}

.empty-state {
  color: #999;
  font-size: 0.9rem;
  padding: 1rem;
  text-align: center;
}

.inspector-signal-block {
  border: 1px solid #ddd;
  border-radius: 4px;
  background: #fafafa;
  overflow: hidden;
}

.block-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem;
  cursor: pointer;
  user-select: none;
}

.block-header:hover {
  background: #f0f0f0;
}

.header-left {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  flex: 1;
}

.block-title {
  font-weight: 600;
  font-size: 0.95rem;
}

.block-label {
  font-size: 0.8rem;
  color: #666;
  background: #e8e8e8;
  padding: 0.2rem 0.4rem;
  border-radius: 2px;
}

.chevron {
  display: inline-block;
  transition: transform 0.2s;
  font-size: 1.2rem;
  color: #666;
}

.chevron.open {
  transform: rotate(90deg);
}

.block-content {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 0.75rem;
  border-top: 1px solid #ddd;
  background: #fff;
}

.field-group {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.field-group label {
  font-size: 0.8rem;
  font-weight: 600;
  color: #333;
}

.field-group input,
.field-group textarea {
  padding: 0.5rem;
  border: 1px solid #ccc;
  border-radius: 3px;
  font-family: inherit;
  font-size: 0.9rem;
}

.field-group input:focus,
.field-group textarea:focus {
  outline: none;
  border-color: #1976d2;
  box-shadow: 0 0 0 2px rgba(25, 118, 210, 0.1);
}

.block-footer {
  display: flex;
  justify-content: flex-end;
  padding-top: 0.5rem;
  border-top: 1px solid #eee;
}

.add-button {
  flex: 1;
  padding: 0.5rem;
  border-radius: 6px;
  border: 1px dashed #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 0.82rem;
  cursor: pointer;
  margin-top: 0.5rem;
}

.add-button:hover {
  background: #eef2f9;
}
</style>
