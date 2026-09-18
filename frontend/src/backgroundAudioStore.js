import { ref } from 'vue'

export const backgroundAudioUrl = ref(null)
export const backgroundAudioPlaying = ref(false)

let audioEl = null

function audioElFor(url) {
  if (!audioEl) {
    audioEl = new Audio()
    audioEl.loop = true
  }
  if (audioEl.src !== url) audioEl.src = url
  return audioEl
}

export function playBackgroundAudio(url) {
  audioEl?.pause()
  backgroundAudioUrl.value = url
  backgroundAudioPlaying.value = false
}

export function stopBackgroundAudio() {
  audioEl?.pause()
  backgroundAudioUrl.value = null
  backgroundAudioPlaying.value = false
}

export function toggleBackgroundAudio() {
  if (!backgroundAudioUrl.value) return
  const el = audioElFor(backgroundAudioUrl.value)
  if (backgroundAudioPlaying.value) {
    el.pause()
    backgroundAudioPlaying.value = false
  } else {
    el.play().catch(() => {})
    backgroundAudioPlaying.value = true
  }
}
