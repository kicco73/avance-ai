<script setup>
import { ref } from 'vue'
import { startRecording, stopRecording } from '../mic.js'
import { postListenTranscribe } from '../api.js'
import { configured } from '../availability.js'
import { setApiError } from '../../../errorStore.js'
import { unlockAudioPlayback } from '../../../audio.js'

const props = defineProps({
  store: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
  sample: { type: Boolean, default: false }
})

const recording = ref(false)

async function startPtt(event) {
  if (event?.pointerType === 'mouse' && event.button !== 0) return
  if (recording.value || props.disabled) return
  unlockAudioPlayback()
  if (!navigator.mediaDevices?.getUserMedia) {
    setApiError(
      'Microphone unavailable.',
      window.isSecureContext ? undefined : 'This page must be loaded over https to use the microphone.'
    )
    return
  }
  try {
    await startRecording()
    recording.value = true
  } catch (err) {
    setApiError('Microphone access was denied.', err.message)
  }
}

async function stopPtt() {
  if (!recording.value) return
  recording.value = false
  const blob = await stopRecording()
  if (!blob?.size) return
  const pending = props.store.beginVoiceMessage()
  let text
  try {
    text = (await postListenTranscribe(blob)).text?.trim()
  } catch {
    pending.abandoned()
    return
  }
  if (!text) {
    pending.abandoned()
    return
  }
  await pending.transcribed(text)
}
</script>

<template>
  <button
    v-if="configured || sample"
    type="button"
    class="chat-input-control mic-btn"
    :class="{ 'mic-btn-recording': recording }"
    :disabled="!recording && disabled"
    :title="recording ? 'Release to send' : 'Hold to record a voice message'"
    @pointerdown.prevent="startPtt"
    @pointerup="stopPtt"
    @pointerleave="stopPtt"
    @pointercancel="stopPtt"
    @contextmenu.prevent
  >
    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
      <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3z" />
      <path d="M17 11a1 1 0 1 0-2 0 3 3 0 0 1-6 0 1 1 0 1 0-2 0 5 5 0 0 0 4 4.9V18H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-2.1A5 5 0 0 0 17 11z" />
    </svg>
  </button>
</template>

<style scoped>
.mic-btn {
  touch-action: none;
  -webkit-user-select: none;
  user-select: none;
}

.mic-btn-recording {
  background: #c0392b;
  border-color: #c0392b;
  color: white;
}

.mic-btn-recording:hover:not(:disabled) {
  background: #a93226;
}
</style>
