// A single short sine-wave "pop" for an arriving AI reply — synthesized via
// Web Audio API rather than an audio asset, so there's no file to license or
// ship. Deliberately quiet and un-notification-like: this is a clinical
// harm-reduction app, not a social/gaming product, so the chime must never
// read as gamified engagement bait.
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

// Fetches and plays a message's generated narration, if any. A missing
// audio (404 — never generated, wrong provider, or already purged; see
// backend/audio_store.py) must fail silently: no visible error, this is a
// best-effort nicety layered on top of the text that's already shown.
export function playMessageAudio(url) {
  try {
    const audio = new Audio(url)
    audio.addEventListener('error', () => {}) // swallow a 404/decode failure quietly
    audio.play().catch(() => {}) // autoplay can also be blocked by the browser itself
  } catch {
    // same tolerance as playMessageChime above
  }
}
