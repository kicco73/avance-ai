import { notify } from '../../toastStore.js'
import { getAudioContext } from '../../audio.js'

function audioDebug(message) {
  if (window.location.hash !== '#audiodebug') return
  notify('audiodebug', message)
}

const scheduledSources = []

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
    }
  }
}

const HEADER_SIZE_BYTES = 44
const MIN_CHUNK_DURATION_SECONDS = 0.2
const SCHEDULE_LEAD_SECONDS = 0.05

export async function playMessageAudio(url) {
  stopNarration()
  const generation = currentGeneration
  const controller = new AbortController()
  currentAbortController = controller

  try {
    const response = await fetch(url, { signal: controller.signal, credentials: 'include' })
    audioDebug(`fetch ${response.status}`)
    if (!response.ok || generation !== currentGeneration) return

    const ctx = getAudioContext()
    const reader = response.body.getReader()

    let headerBuffer = new Uint8Array(0)
    let sampleRate = null
    let minChunkSamples = 0
    let leftoverByte = null
    let nextStartTime = ctx.currentTime + SCHEDULE_LEAD_SECONDS
    let pendingChunks = []
    let pendingSampleCount = 0
    let scheduledCount = 0
    let streamEnded = false

    function reportPlaybackEndedIfDone() {
      if (streamEnded && scheduledSources.length === 0) {
        audioDebug(`playback ended, ${scheduledCount} chunks`)
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
          audioDebug('header: unrecognized WAV layout')
          return
        }
        const channels = view.getUint16(22, true)
        const bitsPerSample = view.getUint16(34, true)
        if (channels !== 1 || bitsPerSample !== 16) {
          audioDebug(`header: unsupported format (channels=${channels}, bits=${bitsPerSample})`)
          return
        }
        sampleRate = view.getUint32(24, true)
        minChunkSamples = Math.round(MIN_CHUNK_DURATION_SECONDS * sampleRate)
        audioDebug(`header sampleRate=${sampleRate}`)
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
      audioDebug('aborted')
    } else {
      audioDebug(`stream error: ${err?.name ?? 'failed'}`)
    }
  }
}
