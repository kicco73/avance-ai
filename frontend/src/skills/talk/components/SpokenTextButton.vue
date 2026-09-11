<script setup>
import { spokenTextEnabled, toggleSpokenText } from '../../../chatPreferences.js'
import { configured } from '../availability.js'

defineProps({
  store: { type: Object, required: true },
  disabled: { type: Boolean, default: false }
})
</script>

<template>
  <button
    v-if="configured"
    type="button"
    class="chat-input-control spoken-text-btn"
    :class="{ 'spoken-text-btn-on': spokenTextEnabled }"
    :title="spokenTextEnabled ? 'Showing spoken text' : 'Show spoken text'"
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
