import { ref } from 'vue'

export const backgroundAudioUrl = ref(null)

export function playBackgroundAudio(url) {
  backgroundAudioUrl.value = url
}

export function stopBackgroundAudio() {
  backgroundAudioUrl.value = null
}
