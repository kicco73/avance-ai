import { apiFetch } from '../../api/core.js'

const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function postListenTranscribe(audioBlob) {
  const formData = new FormData()
  formData.append('file', audioBlob, 'recording.webm')
  return apiFetch(`${API_URL}/skills/listen/transcribe`, {
    method: 'POST',
    body: formData
  })
}
