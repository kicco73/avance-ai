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
}

.action-btn {
  padding: 0.4rem 0.9rem;
  border-radius: 6px;
  font-size: 0.85rem;
  cursor: pointer;
}

/* An unbounded flex-wrap can grow to several rows before it runs out of
   actions, eating the transcript's own height with no cap — a single
   horizontally-scrollable row instead, same pattern as a mobile chip
   bar. Tighter padding claws back some of the footer's own share of a
   short screen. */
@media (max-width: 640px) {
  .action-buttons {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding: 0.5rem 0.75rem;
  }

  .action-btn {
    flex: none;
  }
}

@media (hover: none) and (pointer: coarse) {
  .action-btn {
    min-height: 2.75rem;
  }
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>

<!-- Unscoped, deliberately: a scoped rule's [data-v-xxx] attribute selector
     always outranks an equal-specificity .action-buttons/.action-btn rule
     from a project's own index.css skin, regardless of load order — same
     issue ChatView.vue's .chat-header/.chat-body/.chat-footer sidestep by
     carrying no color of their own at all. These colors need a real
     default, so they live here instead: no scoping attribute means a
     skin's own same-specificity selector wins on source order (its <style>
     tag is appended to <head> well after this one). -->
<style>
.action-buttons {
  background: #f5f5f7;
}

.action-btn {
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
}

.action-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}
</style>
