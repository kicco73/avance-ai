<script setup>
defineProps({
  actions: {
    type: Array,
    default: () => []
  },
  disabled: {
    type: Boolean,
    default: false
  },
  autoTrackingEnabled: {
    type: Boolean,
    default: false
  },
  // Whether the CURRENT state allows auto-tracking (see backend
  // State.autotracking) — distinct from the global autoTrackingEnabled
  // toggle above. When the state itself opts out, auto-tracking can
  // never drive a transition there no matter the global toggle, so the
  // buttons must stay visible regardless of it.
  stateAutotracking: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['action'])
</script>

<template>
  <div class="action-buttons" v-if="actions.length && (!autoTrackingEnabled || !stateAutotracking)">
    <button
      v-for="action in actions"
      :key="action.name"
      class="action-btn"
      :disabled="disabled"
      @click="emit('action', action.name)"
    >
      {{ action.button_text }}
    </button>
  </div>
</template>

<style scoped>
.action-buttons {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  padding: 0.75rem 1rem;
  background: #f5f5f7;
}

.action-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 0.85rem;
  cursor: pointer;
}

.action-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
