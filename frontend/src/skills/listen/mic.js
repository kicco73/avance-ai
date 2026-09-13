let mediaRecorder = null
let chunks = []

export async function startRecording() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  chunks = []
  mediaRecorder = new MediaRecorder(stream)
  mediaRecorder.addEventListener('dataavailable', (event) => {
    if (event.data.size) chunks.push(event.data)
  })
  mediaRecorder.start()
}

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
