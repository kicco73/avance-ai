<script setup>
import { audioEnabled } from '../../../chatPreferences.js'
import { unlockAudioPlayback } from '../../../audio.js'
import { configured } from '../availability.js'
import { narrator } from '../narrator.js'

const props = defineProps({
  store: { type: Object, required: true },
  disabled: { type: Boolean, default: false }
})

function toggle() {
  const enabled = props.store.toggleAudio()
  if (!enabled) return
  // Inside this same click gesture — every narration from here on,
  // including the one about to play below, happens well outside one.
  unlockAudioPlayback()
  narrator.narrateLatest(props.store.messages.value)
}
</script>

<template>
  <button
    v-if="configured"
    type="button"
    class="chat-input-control audio-btn"
    :class="{ 'audio-btn-on': audioEnabled }"
    :title="audioEnabled ? 'Audio: On' : 'Audio: Off'"
    @click="toggle"
  >
    <svg v-if="audioEnabled" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M3 9v6h4l5 5V4L7 9H3z" />
      <path d="M14.5 3.23v2.06c2.89 1.2 5 4.03 5 7.71s-2.11 6.51-5 7.71v2.06c4.01-1.28 7-5.09 7-9.77s-2.99-8.49-7-9.77zM16.5 12c0-1.77-.77-3.29-2-4.34v8.68c1.23-1.05 2-2.57 2-4.34z" />
    </svg>
    <svg v-else viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M19.73 21 21 19.73l-9-9L4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.8L19.73 21zM19 12c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89 1.2 5 4.03 5 7.71zm-4.5-2.51v.13l2.44 2.44c.04-.28.06-.56.06-.85 0-1.77-.77-3.29-2-4.34-.16-.14-.33-.27-.5-.38z" />
    </svg>
  </button>
</template>

<style scoped>
.audio-btn-on {
  background: #4a6fa5;
  border-color: #4a6fa5;
  color: white;
}
</style>
