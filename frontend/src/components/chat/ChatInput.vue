<script setup>
// The text input plus its side buttons (mic / audio / spoken-text) — a
// self-contained, presentational piece. All chat business logic stays in
// the parent; this component only renders and emits.
import { ref } from 'vue'

defineProps({
  disabled: { type: Boolean, default: false },
  recording: { type: Boolean, default: false },
  micAvailable: { type: Boolean, default: false },
  talkAvailable: { type: Boolean, default: false },
  audioEnabled: { type: Boolean, default: false },
  spokenTextEnabled: { type: Boolean, default: false }
})

const draft = defineModel({ type: String, default: '' })

const emit = defineEmits(['submit', 'mic-start', 'mic-stop', 'toggle-audio', 'toggle-spoken-text'])

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
      :disabled="disabled"
    />

    <button
      v-if="micAvailable"
      type="button"
      class="mic-btn"
      :class="{ 'mic-btn-recording': recording }"
      :disabled="!recording && disabled"
      :title="recording ? 'Release to send' : 'Hold to record a voice message'"
      @pointerdown.prevent="emit('mic-start', $event)"
      @pointerup="emit('mic-stop')"
      @pointerleave="emit('mic-stop')"
      @pointercancel="emit('mic-stop')"
      @contextmenu.prevent
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z" />
        <path d="M17 11a1 1 0 1 0-2 0 3 3 0 0 1-6 0 1 1 0 1 0-2 0 5 5 0 0 0 4 4.9V18H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-2.1A5 5 0 0 0 17 11z" />
      </svg>
    </button>

    <button
      v-if="talkAvailable"
      type="button"
      class="audio-btn"
      :class="{ 'audio-btn-on': audioEnabled }"
      :title="audioEnabled ? 'Audio: On' : 'Audio: Off'"
      @click="emit('toggle-audio')"
    >
      <svg v-if="audioEnabled" viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M3 9v6h4l5 5V4L7 9H3z" />
        <path d="M14.5 3.23v2.06c2.89 1.2 5 4.03 5 7.71s-2.11 6.51-5 7.71v2.06c4.01-1.28 7-5.09 7-9.77s-2.99-8.49-7-9.77zM16.5 12c0-1.77-.77-3.29-2-4.34v8.68c1.23-1.05 2-2.57 2-4.34z" />
      </svg>
      <svg v-else viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M3 9v6h4l5 5V4L7 9H3z" />
        <path d="M19.73 21 21 19.73l-9-9L4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.8L19.73 21zM19 12c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89 1.2 5 4.03 5 7.71zm-4.5-2.51v.13l2.44 2.44c.04-.28.06-.56.06-.85 0-1.77-.77-3.29-2-4.34-.16-.14-.33-.27-.5-.38z" />
      </svg>
    </button>

    <button
      v-if="talkAvailable"
      type="button"
      class="spoken-text-btn"
      :class="{ 'spoken-text-btn-on': spokenTextEnabled }"
      :title="spokenTextEnabled ? 'Showing spoken text' : 'Show spoken text'"
      @click="emit('toggle-spoken-text')"
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
        <path d="M19 4H5c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-8 7.5H9.5v-.5h-2v2h2v-.5H11V15c0 .55-.45 1-1 1H6c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1h4c.55 0 1 .45 1 1v1.5zm7 0h-1.5v-.5h-2v2h2v-.5H18V15c0 .55-.45 1-1 1h-4c-.55 0-1-.45-1-1V9c0-.55.45-1 1-1h4c.55 0 1 .45 1 1v1.5z" />
      </svg>
    </button>
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
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  font-size: 0.95rem;
}

.mic-btn,
.audio-btn,
.spoken-text-btn {
  flex: none;
  width: 2.5rem;
  border-radius: 6px;
  border: 1px solid #999;
  background: white;
  color: #666;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.mic-btn {
  touch-action: none;
  user-select: none;
}

.mic-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.mic-btn-recording {
  border-color: #c62828;
  background: #c62828;
  color: white;
  animation: mic-pulse 1.2s ease-in-out infinite;
}

.mic-btn-recording:hover {
  background: #a02020;
}

.mic-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

@keyframes mic-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(198, 40, 40, 0.5); }
  50% { box-shadow: 0 0 0 6px rgba(198, 40, 40, 0); }
}

.audio-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.audio-btn-on {
  border-color: #2e7d32;
  background: #2e7d32;
  color: white;
}

.audio-btn-on:hover:not(:disabled) {
  background: #256428;
}

.audio-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.spoken-text-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.spoken-text-btn-on {
  border-color: #2e7d32;
  background: #2e7d32;
  color: white;
}

.spoken-text-btn-on:hover:not(:disabled) {
  background: #256428;
}
</style>
