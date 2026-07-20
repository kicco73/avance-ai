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
  }
})

const emit = defineEmits(['action'])
</script>

<template>
  <div class="action-buttons" v-if="actions.length">
    <button
      v-for="action in actions"
      :key="action.name"
      class="action-btn"
      :disabled="disabled || autoTrackingEnabled"
      :title="autoTrackingEnabled ? 'Disable auto-tracking to trigger manually' : ''"
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
  padding: 0 1rem 0.75rem 1rem;
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
