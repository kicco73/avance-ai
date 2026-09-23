import { toRaw } from 'vue'

export function transcriptOf(store) {
  return {
    state: toRaw(store.state.value) ?? null,
    messages: store.messages.value.map((m) => ({
      id: m.id,
      messageId: m.messageId ?? null,
      role: m.role,
      content: m.content,
      timestamp: m.timestamp,
      audioText: m.audioText ?? null,
      awaitingReply: m.awaitingReply ?? false,
      pending: m.pending ?? false
    }))
  }
}
