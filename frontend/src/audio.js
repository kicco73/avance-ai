// iOS Safari rejects any AudioContext/Audio playback that isn't
// triggered directly inside a user gesture (NotAllowedError — swallowed
// below by the existing try/catch tolerance, so it reads as silence with
// no error at all) — and unlocks playback per *element*/*context*, not
// per page, so a brand new one created for every message is never
// unlocked even after an earlier one already was. Shared singletons
// instead of one per call: created lazily, unlocked once inside a real
// gesture (see unlockAudioPlayback below, and its own call sites — chat
// submit, mic-start, audio toggle turning on), reused for every later
// chime/narration with no gesture requirement of their own after that.

let sharedAudioContext = null

function getAudioContext() {
  if (!sharedAudioContext) sharedAudioContext = new (window.AudioContext || window.webkitAudioContext)()
  if (sharedAudioContext.state === 'suspended') sharedAudioContext.resume()
  return sharedAudioContext
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

// Single shared element, not one per playMessageAudio() call — see this
// file's own top comment for why a fresh element per message defeats
// iOS's per-element unlock.
let narrationAudio = null

function getNarrationAudio() {
  if (!narrationAudio) narrationAudio = new Audio()
  return narrationAudio
}

function stopCurrentAudio() {
  if (!narrationAudio) return
  narrationAudio.pause()
  narrationAudio.removeAttribute('src')
  narrationAudio.load()
}

// Fetches and plays a message's generated narration, if any. A missing
// audio (404 — no narration for this message) fails silently: a
// best-effort nicety on top of the text that's already shown.
export function playMessageAudio(url) {
  try {
    stopCurrentAudio()
    const audio = getNarrationAudio()
    audio.addEventListener('error', () => {}, { once: true }) // swallow a 404/decode failure quietly
    audio.src = url
    audio.play().catch(() => {}) // autoplay can also be blocked by the browser itself
  } catch {
    // same tolerance as playMessageChime above
  }
}

// A handful of silent PCM samples, base64-encoded as a WAV — played and
// immediately stopped purely to unlock narrationAudio under iOS's
// per-element playback policy (see unlockAudioPlayback below); never
// actually audible.
const SILENT_WAV_DATA_URI = 'data:audio/wav;base64,UklGRiwAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQgAAACAgICAgICAgA=='

// Set only once the silent clip's play() has actually resolved — never
// eagerly before that. An earlier version set this straight away, before
// knowing whether play() would even succeed; a rejected/blocked attempt
// (silently swallowed by the catch below) then left it stuck true
// forever, since nothing ever unset it again — permanently skipping
// every later gesture's own retry, with no error to reveal why. Chimes
// never had this failure mode: getAudioContext() re-checks/resumes on
// every single call instead of gating behind a "did this ever succeed"
// flag at all.
let narrationUnlocked = false

// Call only from inside a real user gesture (chat submit, mic-start, or
// the audio toggle switching on — see their own call sites); unlocking
// iOS's playback policy requires that, and calling this outside one is a
// silent no-op at best. Safe to call every time, repeatedly — a
// no-op once narrationUnlocked is actually true, and otherwise just
// replays the (inaudible) silent clip again, which costs nothing.
export function unlockAudioPlayback() {
  try {
    getAudioContext()
  } catch {
    // Same tolerance as playMessageChime above.
  }
  if (narrationUnlocked) return
  try {
    const audio = getNarrationAudio()
    audio.src = SILENT_WAV_DATA_URI
    audio.play().then(() => {
      narrationUnlocked = true
      audio.pause()
      audio.removeAttribute('src')
      audio.load()
    }).catch(() => {
      // Rejected/blocked — narrationUnlocked stays false, so the next
      // gesture (there's always another: every message send calls this
      // too) tries again instead of giving up silently for good.
    })
  } catch {
    // Same tolerance as playMessageChime above — and, same as the
    // rejection above, narrationUnlocked is never set here either.
  }
}
