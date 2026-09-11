<script setup>
import { inject, ref } from 'vue'

defineProps({
  prompt: { type: Object, required: true }
})

// Resolves customDialog()'s own promise with the chosen state — or with
// null through the shared × / ESC / backdrop, which is the cancel.
const closeDialog = inject('closeDialog')

const choice = ref('')
</script>

<template>
  <h2 class="remap-title">Publish</h2>
  <p class="remap-body">
    The conversation's own current state ("{{ prompt.missing_state }}") no longer exists in this
    revision. Pick the state it now corresponds to before publishing.
  </p>
  <select v-model="choice" class="remap-select">
    <option disabled value="">Select a state…</option>
    <option v-for="key in prompt.available_states" :key="key" :value="key">{{ key }}</option>
  </select>
  <div class="remap-actions">
    <button class="remap-publish-btn" :disabled="!choice" @click="closeDialog(choice)">Publish</button>
    <button class="remap-cancel-btn" @click="closeDialog(null)">Cancel</button>
  </div>
</template>

<style scoped>
.remap-title {
  margin: 0 0 0.6rem;
  padding-right: 1.6rem; /* clears DialogHost's own × close button */
  font-size: 1.05rem;
  font-weight: 600;
  color: #333;
}

.remap-body {
  margin: 0 0 1rem;
  font-size: 0.9rem;
  color: #333;
}

.remap-select {
  display: block;
  width: 100%;
  margin-bottom: 1rem;
  padding: 0.4rem 0.5rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  font-size: 0.85rem;
}

.remap-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

.remap-publish-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  cursor: pointer;
  font-size: 0.85rem;
}

.remap-publish-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.remap-cancel-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  background: white;
  color: #444;
  cursor: pointer;
  font-size: 0.85rem;
}
</style>
