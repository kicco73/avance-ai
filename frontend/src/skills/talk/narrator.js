import { audioEnabled } from '../../chatPreferences.js'
import { playMessageAudio } from './playback.js'
import { messageAudioUrl } from './api.js'

export const narrator = {
  messageArrived(messageId) {
    if (!audioEnabled.value) return
    playMessageAudio(messageAudioUrl(messageId))
  },

  narrateLatest(messages) {
    const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant' && m.messageId != null)
    if (lastAssistant) playMessageAudio(messageAudioUrl(lastAssistant.messageId))
  }
}
