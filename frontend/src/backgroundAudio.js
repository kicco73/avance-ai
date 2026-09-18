import { ref } from 'vue'

export function createBackgroundAudio() {
  const backgroundAudioUrl = ref(null)
  const backgroundAudioPlaying = ref(false)
  let audioEl = null

  function play(url) {
    audioEl?.pause()
    backgroundAudioUrl.value = url
    backgroundAudioPlaying.value = false
  }

  function stop() {
    audioEl?.pause()
    backgroundAudioUrl.value = null
    backgroundAudioPlaying.value = false
  }

  function toggle() {
    if (!backgroundAudioUrl.value) return
    if (!audioEl) {
      audioEl = new Audio()
      audioEl.loop = true
    }
    if (audioEl.src !== backgroundAudioUrl.value) audioEl.src = backgroundAudioUrl.value
    if (backgroundAudioPlaying.value) {
      audioEl.pause()
      backgroundAudioPlaying.value = false
    } else {
      audioEl.play().catch(() => {})
      backgroundAudioPlaying.value = true
    }
  }

  return { backgroundAudioUrl, backgroundAudioPlaying, playBackgroundAudio: play, stopBackgroundAudio: stop, toggleBackgroundAudio: toggle }
}
