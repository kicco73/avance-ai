export function playMessageChime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'sine'
    osc.frequency.value = 720
    gain.gain.setValueAtTime(0.001, ctx.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + 0.01)
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15)
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start()
    osc.stop(ctx.currentTime + 0.15)
  } catch {
    // Audio is a nicety, never a hard requirement — a blocked/unsupported
    // AudioContext (e.g. no prior user interaction) must not break chat.
  }
}

// Tracks whichever narration is currently playing, if any, so a new one
// never overlaps a still-running previous one.
let currentAudio = null

function stopCurrentAudio() {
  if (!currentAudio) return
  currentAudio.pause()
  currentAudio.removeAttribute('src')
  currentAudio.load()
  currentAudio = null
}

// Fetches and plays a message's generated narration, if any. A missing
// audio (404 — no [audio] tag on this message; see backend/talk/talk_service.py)
// must fail silently: no visible error, a best-effort nicety on top of the
// text that's already shown.
export function playMessageAudio(url) {
  try {
    stopCurrentAudio()
    const audio = new Audio(url)
    currentAudio = audio
    audio.addEventListener('error', () => {}) // swallow a 404/decode failure quietly
    audio.addEventListener('ended', () => {
      if (currentAudio === audio) currentAudio = null
    })
    audio.play().catch(() => {}) // autoplay can also be blocked by the browser itself
  } catch {
    // same tolerance as playMessageChime above
  }
}
