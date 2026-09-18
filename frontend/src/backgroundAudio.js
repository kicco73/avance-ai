import { ref } from 'vue'

const FADE_MS = 200
const FADE_STEPS = 10

let sharedAudioEl = null
let activePlayer = null
let fadeTimer = null

function sharedAudio() {
  if (!sharedAudioEl) {
    sharedAudioEl = new Audio()
    sharedAudioEl.loop = true
  }
  return sharedAudioEl
}

function cancelFade() {
  if (!fadeTimer) return
  clearInterval(fadeTimer)
  fadeTimer = null
}

function fadeOutAndPause(el) {
  cancelFade()
  const startVolume = el.volume
  let step = 0
  fadeTimer = setInterval(() => {
    step += 1
    if (step >= FADE_STEPS) {
      cancelFade()
      el.pause()
      el.volume = 1
      return
    }
    el.volume = Math.max(0, startVolume * (1 - step / FADE_STEPS))
  }, FADE_MS / FADE_STEPS)
}

export function createBackgroundAudio() {
  const backgroundAudioUrl = ref(null)
  const backgroundAudioPlaying = ref(false)

  function releaseIfOwner() {
    if (activePlayer !== backgroundAudioPlaying) return
    fadeOutAndPause(sharedAudio())
    activePlayer = null
  }

  function play(url) {
    releaseIfOwner()
    backgroundAudioUrl.value = url
    backgroundAudioPlaying.value = false
  }

  function stop() {
    releaseIfOwner()
    backgroundAudioUrl.value = null
    backgroundAudioPlaying.value = false
  }

  function pause() {
    releaseIfOwner()
    backgroundAudioPlaying.value = false
  }

  function toggle() {
    if (!backgroundAudioUrl.value) return
    if (backgroundAudioPlaying.value) {
      pause()
      return
    }
    if (activePlayer) activePlayer.value = false
    cancelFade()
    const el = sharedAudio()
    el.pause()
    el.volume = 1
    el.src = backgroundAudioUrl.value
    el.play().catch(() => {})
    backgroundAudioPlaying.value = true
    activePlayer = backgroundAudioPlaying
  }

  return {
    backgroundAudioUrl, backgroundAudioPlaying,
    playBackgroundAudio: play, stopBackgroundAudio: stop, pauseBackgroundAudio: pause, toggleBackgroundAudio: toggle,
  }
}
