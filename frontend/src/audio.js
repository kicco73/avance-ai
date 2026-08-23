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

// A soft, low-pitched notification for a reaction landing on a message —
// deliberately quieter and lower than playMessageChime's own bright "new
// reply" ping, so it reads as a discreet aside, not another alert. Two
// short notes, root then a major third above (e.g. C -> E), not a single
// held tone.
export function playReactionChime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const noteDuration = 0.12
    const notes = [261.63, 329.63] // C4, then E4 (major third above)
    notes.forEach((frequency, i) => {
      const start = ctx.currentTime + i * noteDuration
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = frequency
      gain.gain.setValueAtTime(0.001, start)
      gain.gain.exponentialRampToValueAtTime(0.06, start + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.001, start + noteDuration)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start(start)
      osc.stop(start + noteDuration)
    })
  } catch {
    // Same tolerance as playMessageChime above.
  }
}

// Tracks the currently playing narration, if any, so a new one never
// overlaps a still-running previous one.
let currentAudio = null

function stopCurrentAudio() {
  if (!currentAudio) return
  currentAudio.pause()
  currentAudio.removeAttribute('src')
  currentAudio.load()
  currentAudio = null
}

// Fetches and plays a message's generated narration, if any. A missing
// audio (404 — no narration for this message) fails silently: a
// best-effort nicety on top of the text that's already shown.
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
