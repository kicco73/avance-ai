<script setup>
import { ref } from 'vue'

defineProps({
  prompt: { type: Object, required: true },
  publishing: { type: Boolean, default: false }
})

const emit = defineEmits(['confirm', 'cancel'])

const choice = ref('')
</script>

<template>
  <div class="switch-dialog-overlay">
    <div class="switch-dialog">
      <p>
        The conversation's own current state ("{{ prompt.missing_state }}") no longer exists in this
        revision. Pick the state it now corresponds to before publishing.
      </p>
      <select v-model="choice" class="remap-select">
        <option disabled value="">Select a state…</option>
        <option v-for="key in prompt.available_states" :key="key" :value="key">{{ key }}</option>
      </select>
      <div class="switch-dialog-actions">
        <button
          class="switch-dialog-save-btn"
          :disabled="publishing || !choice"
          @click="emit('confirm', choice)"
        >Publish</button>
        <button class="switch-dialog-cancel-btn" :disabled="publishing" @click="emit('cancel')">Cancel</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.remap-select {
  display: block;
  width: 100%;
  margin-bottom: 1rem;
  padding: 0.4rem 0.5rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  font-size: 0.85rem;
}

.switch-dialog-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  background: rgba(0, 0, 0, 0.35);
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
}

.switch-dialog {
  background: white;
  border-radius: 10px;
  padding: 1.2rem;
  max-width: 360px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
}

.switch-dialog p {
  margin: 0 0 1rem;
  font-size: 0.9rem;
  color: #333;
}

.switch-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

.switch-dialog-save-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  cursor: pointer;
  font-size: 0.85rem;
}

.switch-dialog-save-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.switch-dialog-cancel-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  background: white;
  color: #444;
  cursor: pointer;
  font-size: 0.85rem;
}

.switch-dialog-cancel-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
