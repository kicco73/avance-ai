const API_URL = import.meta.env.VITE_API_URL ?? '/api'

export function messageAudioUrl(messageId) {
  return `${API_URL}/skills/talk/messages/${messageId}/audio`
}
