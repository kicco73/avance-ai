<script setup>
import { computed } from 'vue'

import { spokenTextEnabled, toggleSpokenText } from '../../../chatPreferences.js'
import { configured } from '../availability.js'

const props = defineProps({
  store: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
  // Shown regardless of what this conversation can reach: a row that is
  // standing in for a chat (see ChatInput.vue's own `sample`).
  sample: { type: Boolean, default: false }
})

// A sample row shows what a chat looks like, not what this person has
// switched on: its controls are drawn in their resting state (see
// ChatInput.vue's own `sample`).
const on = computed(() => !props.sample && spokenTextEnabled.value)
</script>

<template>
  <button
    v-if="configured || sample"
    type="button"
    class="chat-input-control spoken-text-btn"
    :disabled="disabled"
    :class="{ 'spoken-text-btn-on': on }"
    :title="on ? 'Showing spoken text' : 'Show spoken text'"
    @click="toggleSpokenText"
  >
    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M19 4H5c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-8 7.5H9.5v-.5h-2v2h2v-.5H11V15c0 .55-.45 1-1 1H6c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1h4c.55 0 1 .45 1 1v1.5zm7 0h-1.5v-.5h-2v2h2v-.5H18V15c0 .55-.45 1-1 1h-4c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1h4c.55 0 1 .45 1 1v1.5z" />
    </svg>
  </button>
</template>

<style scoped>
.spoken-text-btn-on {
  background: #4a6fa5;
  border-color: #4a6fa5;
  color: white;
}
</style>
