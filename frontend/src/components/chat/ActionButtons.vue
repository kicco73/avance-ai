<script setup>
import { computed } from 'vue'

const props = defineProps({
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

// Actions with a trigger are handled by auto-tracking when it's on, so
// only triggerless (manual-only) actions get a button in that case.
const visibleActions = computed(() =>
  props.actions.filter((action) => !action.has_trigger || !props.autoTrackingEnabled)
)
</script>

<template>
  <div class="action-buttons" v-if="visibleActions.length">
    <button
      v-for="action in visibleActions"
      :key="action.name"
      class="action-btn"
      :disabled="disabled"
      @click="emit('action', action.name)"
    >
      {{ action.ui_button }}
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
