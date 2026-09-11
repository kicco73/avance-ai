// iOS Safari rejects any AudioContext playback that isn't triggered
// directly inside a user gesture (NotAllowedError), which is what
// unlockAudioPlayback below is for. The context is shared: the chimes
// here and whatever else plays sound in this build use the same one.

let sharedAudioContext = null

export function getAudioContext() {
  if (!sharedAudioContext) sharedAudioContext = new (window.AudioContext || window.webkitAudioContext)()
  if (sharedAudioContext.state === 'suspended') sharedAudioContext.resume()
  return sharedAudioContext
}

// Call only from inside a real user gesture (chat submit, mic-start, or
// the audio toggle switching on — see their own call sites); unlocking
// iOS's playback policy requires that, and calling this outside one is a
// silent no-op at best. Safe to call every time, repeatedly.
export function unlockAudioPlayback() {
  try {
    getAudioContext()
  } catch {
    // Audio is a nicety, never a hard requirement — a blocked/unsupported
    // AudioContext (e.g. no prior user interaction) must not break chat.
  }
}

export function playMessageChime() {
  try {
    const ctx = getAudioContext()
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
    const ctx = getAudioContext()
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
