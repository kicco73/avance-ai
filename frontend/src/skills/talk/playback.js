import { notify } from '../../toastStore.js'
import { getAudioContext } from '../../audio.js'

// iOS Safari rejects any AudioContext playback that isn't triggered
// directly inside a user gesture (NotAllowedError) — and, separately,
// can't play narration from /api/skills/talk/messages/{id}/audio through an
// HTMLAudioElement at all: that endpoint is a WAV with an indeterminate
// length ("streaming") header (see backend's talk_format.py
// PcmWavCodec.streaming_header — RIFF/data chunk sizes are the
// 0xFFFFFFFF sentinel), and iOS's own media loader opens every
// <audio>/<video> source with a Range: bytes=0-1 probe that an
// indeterminate-length stream can't satisfy, aborting as a media error
// (silently, since the 'error' listener swallows it). Chrome desktop
// tolerates the same stream progressively, which is why this only ever
// showed up on iOS.
//
// Narration is instead consumed as raw bytes here — fetch + a
// ReadableStream reader, the WAV header parsed by hand once (fixed,
// known layout — see streaming_header above; there's no need to walk
// arbitrary RIFF chunks for a header this backend always writes the same
// way), the PCM payload decoded into Float32Array samples and scheduled
// on the shared AudioContext (see getAudioContext below, and the chimes,
// which already use it) as consecutive AudioBuffers — playing back
// progressively, from the first chunk, identically on iOS and desktop,
// with no <audio> element and no Range dependency anywhere.

// TEMP audiodebug: append #audiodebug to the URL to see the streaming
// narration player's own checkpoints (fetch status, header sample rate,
// aborts, stream errors, final chunk count) as toasts on a real device,
// where there's no console open to read otherwise. Remove this whole
// function and its call sites once narration on iOS is confirmed
// reliable in the field.
function audioDebug(message) {
  if (window.location.hash !== '#audiodebug') return
  notify('audiodebug', message)
}

// Every AudioBufferSourceNode currently scheduled (started, not yet
// ended) for the narration in progress — stopNarration() below cancels
// every one of these on a new playMessageAudio() call or an explicit
// stop, which is the non-overlap guarantee the old single <audio>
// element used to give for free.
const scheduledSources = []

// Bumped on every stopNarration() — a fetch/read already in flight reads
// this back after each await and quietly gives up if it's stale, since
// aborting the fetch itself only interrupts whatever read is *currently*
// pending, not any work already queued behind it in the same call.
let currentGeneration = 0
let currentAbortController = null

function stopNarration() {
  currentGeneration++
  currentAbortController?.abort()
  currentAbortController = null
  while (scheduledSources.length) {
    const source = scheduledSources.pop()
    try {
      source.stop()
    } catch {
      // Already ended on its own — stop() on a finished source throws.
    }
  }
}

const HEADER_SIZE_BYTES = 44 // RIFF(12) + fmt chunk(24) + data chunk id/size(8) — see talk_format.py's own streaming_header
const MIN_CHUNK_DURATION_SECONDS = 0.2
const SCHEDULE_LEAD_SECONDS = 0.05

// Fetches and plays a message's generated narration, if any, as a
// progressive PCM stream — see this file's own top comment for why. A
// missing audio (404 — no narration for this message) fails silently: a
// best-effort nicety on top of the text that's already shown.
export async function playMessageAudio(url) {
  stopNarration()
  const generation = currentGeneration
  const controller = new AbortController()
  currentAbortController = controller

  try {
    // credentials: 'include' — see api/core.js's own apiFetch: the
    // session cookie is httpOnly and, in dev especially, this app's
    // frontend/backend often sit on different origins (VITE_API_URL
    // pointing at a separate port), so the browser's own default
    // credentials mode ('same-origin') silently drops the cookie there.
    // Without this, the request 401s — indistinguishable, from in here,
    // from "no narration for this message" (see the !response.ok check
    // below), which is exactly how the previous version of this file
    // went silently wrong on every platform, not just iOS: it assumed
    // same-origin and never sent the cookie at all.
    const response = await fetch(url, { signal: controller.signal, credentials: 'include' })
    audioDebug(`fetch ${response.status}`) // TEMP audiodebug
    if (!response.ok || generation !== currentGeneration) return

    const ctx = getAudioContext()
    const reader = response.body.getReader()

    let headerBuffer = new Uint8Array(0)
    let sampleRate = null
    let minChunkSamples = 0
    let leftoverByte = null // an odd trailing byte from a read that split a 2-byte sample in half
    let nextStartTime = ctx.currentTime + SCHEDULE_LEAD_SECONDS
    let pendingChunks = []
    let pendingSampleCount = 0
    let scheduledCount = 0
    let streamEnded = false

    function reportPlaybackEndedIfDone() {
      if (streamEnded && scheduledSources.length === 0) {
        audioDebug(`playback ended, ${scheduledCount} chunks`) // TEMP audiodebug
      }
    }

    function flushPending(force) {
      if (pendingSampleCount === 0) return
      if (!force && pendingSampleCount < minChunkSamples) return
      const merged = new Float32Array(pendingSampleCount)
      let offset = 0
      for (const chunk of pendingChunks) {
        merged.set(chunk, offset)
        offset += chunk.length
      }
      pendingChunks = []
      pendingSampleCount = 0

      const buffer = ctx.createBuffer(1, merged.length, sampleRate)
      buffer.copyToChannel(merged, 0)
      const source = ctx.createBufferSource()
      source.buffer = buffer
      source.connect(ctx.destination)
      const startAt = Math.max(ctx.currentTime + SCHEDULE_LEAD_SECONDS, nextStartTime)
      source.onended = () => {
        const idx = scheduledSources.indexOf(source)
        if (idx !== -1) scheduledSources.splice(idx, 1)
        reportPlaybackEndedIfDone()
      }
      scheduledSources.push(source)
      source.start(startAt)
      nextStartTime = startAt + buffer.duration
      scheduledCount++
    }

    function addSamples(float32) {
      pendingChunks.push(float32)
      pendingSampleCount += float32.length
      flushPending(false)
    }

    while (true) {
      const { done, value } = await reader.read()
      if (generation !== currentGeneration) return
      if (done) break
      if (!value || value.length === 0) continue

      let bytes = value

      if (sampleRate === null) {
        const combined = new Uint8Array(headerBuffer.length + bytes.length)
        combined.set(headerBuffer)
        combined.set(bytes, headerBuffer.length)
        if (combined.length < HEADER_SIZE_BYTES) {
          headerBuffer = combined
          continue
        }
        const view = new DataView(combined.buffer, combined.byteOffset, HEADER_SIZE_BYTES)
        const magic = (offset) => String.fromCharCode(view.getUint8(offset), view.getUint8(offset + 1), view.getUint8(offset + 2), view.getUint8(offset + 3))
        if (magic(0) !== 'RIFF' || magic(8) !== 'WAVE' || magic(12) !== 'fmt ' || magic(36) !== 'data') {
          audioDebug('header: unrecognized WAV layout') // TEMP audiodebug
          return
        }
        const channels = view.getUint16(22, true)
        const bitsPerSample = view.getUint16(34, true)
        if (channels !== 1 || bitsPerSample !== 16) {
          audioDebug(`header: unsupported format (channels=${channels}, bits=${bitsPerSample})`) // TEMP audiodebug
          return
        }
        sampleRate = view.getUint32(24, true)
        minChunkSamples = Math.round(MIN_CHUNK_DURATION_SECONDS * sampleRate)
        audioDebug(`header sampleRate=${sampleRate}`) // TEMP audiodebug
        bytes = combined.subarray(HEADER_SIZE_BYTES)
      }

      let pcmBytes = bytes
      if (leftoverByte !== null) {
        const merged = new Uint8Array(pcmBytes.length + 1)
        merged[0] = leftoverByte
        merged.set(pcmBytes, 1)
        pcmBytes = merged
        leftoverByte = null
      }
      let usableLength = pcmBytes.length
      if (usableLength % 2 !== 0) {
        leftoverByte = pcmBytes[usableLength - 1]
        usableLength -= 1
      }
      if (usableLength === 0) continue

      const sampleCount = usableLength / 2
      const float32 = new Float32Array(sampleCount)
      const dv = new DataView(pcmBytes.buffer, pcmBytes.byteOffset, usableLength)
      for (let i = 0; i < sampleCount; i++) {
        float32[i] = dv.getInt16(i * 2, true) / 32768
      }
      addSamples(float32)
    }

    if (sampleRate !== null) flushPending(true)
    streamEnded = true
    reportPlaybackEndedIfDone()
  } catch (err) {
    if (err?.name === 'AbortError') {
      audioDebug('aborted') // TEMP audiodebug
    } else {
      audioDebug(`stream error: ${err?.name ?? 'failed'}`) // TEMP audiodebug
    }
  }
}
