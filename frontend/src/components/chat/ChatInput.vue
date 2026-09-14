<script setup>
import { computed, ref } from 'vue'
import { chatInputControls } from '../../skills/registry.js'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  store: { type: Object, required: true },
  sample: { type: Boolean, default: false }
})

const inert = computed(() => props.disabled || props.sample)

const draft = defineModel({ type: String, default: '' })

const emit = defineEmits(['submit'])

const inputRef = ref(null)

defineExpose({ focus: () => inputRef.value?.focus() })
</script>

<template>
  <form class="input-row" @submit.prevent="emit('submit')">
    <input
      ref="inputRef"
      v-model="draft"
      type="text"
      placeholder="Type a message..."
      :disabled="inert"
      enterkeyhint="send"
      autocapitalize="sentences"
      autocomplete="off"
      spellcheck="true"
    />

    <component
      v-for="control in chatInputControls"
      :key="control.id"
      :is="control.component"
      :store="store"
      :disabled="inert"
      :sample="sample"
    />
  </form>
</template>

<style scoped>
.input-row {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-top: 1px solid #ddd;
}

.input-row input {
  flex: 1;
  min-width: 0;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  font-size: 1rem;
}
</style>

<style>
.chat-input-control {
  flex: none;
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 6px;
  border: 1px solid #999;
  background: white;
  color: #666;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.chat-input-control:disabled {
  opacity: 0.5;
  cursor: default;
}

.chat-input-control:hover:not(:disabled) {
  background: #f0f0f0;
}

@media (hover: none) and (pointer: coarse) {
  .chat-input-control {
    width: 2.75rem;
    height: 2.75rem;
  }
}
</style>
