// Microphone capture for the voice-message button — wraps the browser's
// MediaRecorder behind a simple start/stop pair.
let mediaRecorder = null
let chunks = []

// Rejects if the user denies the permission prompt (or no mic exists) —
// the caller surfaces that through the shared error store.
export async function startRecording() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  chunks = []
  mediaRecorder = new MediaRecorder(stream)
  mediaRecorder.addEventListener('dataavailable', (event) => {
    if (event.data.size) chunks.push(event.data)
  })
  mediaRecorder.start()
}

// Resolves with the recorded audio as a Blob (or null if nothing was
// recording), and releases the microphone track so the browser's
// recording indicator turns off.
export function stopRecording() {
  const recorder = mediaRecorder
  mediaRecorder = null
  if (!recorder) return Promise.resolve(null)

  return new Promise((resolve) => {
    recorder.addEventListener('stop', () => {
      recorder.stream.getTracks().forEach((track) => track.stop())
      resolve(new Blob(chunks, { type: recorder.mimeType }))
    })
    recorder.stop()
  })
}
