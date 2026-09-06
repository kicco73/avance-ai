import { onConnectionState, resolvePendingTurnsAfterReload } from './chatClient.js'
import { getMessages, getSessionState } from './api.js'

// A dropped socket loses whatever the server was still sending. The truth
// is in the database, so a reconnection re-reads the open session and
// settles whatever turn was in flight against what actually persisted:
// resolved from the reloaded messages when the turn ran, or failed — its
// own bubble kept on screen, ready to resend — when the user message
// never made it at all. One of these per chat store; `chat` is that
// store's own view of the session it is showing.
export class ChatReconnectSync {
  constructor(chat) {
    this._chat = chat
  }

  register() {
    onConnectionState((state, { reconnected }) => {
      if (state !== 'open' || !reconnected) return
      this.resynchronize()
    })
  }

  async resynchronize() {
    const chat = this._chat
    const sessionId = chat.currentSessionId.value
    if (sessionId == null) return
    let history
    let sessionState
    try {
      [history, sessionState] = await Promise.all([getMessages(sessionId), getSessionState(sessionId)])
    } catch {
      return // already surfaced via apiFetch
    }
    if (chat.currentSessionId.value !== sessionId) return

    // Settled against the rows just fetched, before they replace what is
    // on screen — a turn whose user message never persisted has its own
    // local bubble carried over, so rejecting it can still mark it failed.
    const localBefore = chat.messages.value
    const unsent = []
    resolvePendingTurnsAfterReload((pending) => {
      const rebuilt = this.rebuildTurn(history, pending)
      if (rebuilt === null) {
        const bubble = localBefore.find(
          (m) => m.role === 'user' && m.messageId == null && m.content === pending.text,
        )
        if (bubble) unsent.push(bubble)
      }
      return rebuilt
    })
    chat.messages.value = [...history.map((row) => chat.toStoreMessage(row)), ...unsent]
    chat.state.value = sessionState
  }

  // What a turn interrupted by the drop actually produced, read straight
  // off the reloaded rows: null when that turn's own user message never
  // made it at all.
  rebuildTurn(history, { text }) {
    let userIndex = -1
    history.forEach((row, i) => {
      if (row.role === 'user' && row.content === text) userIndex = i
    })
    if (userIndex === -1) return null
    const assistant = history.slice(userIndex + 1).find((row) => row.role === 'assistant')
    // `reply` stays empty on purpose: the reload below already puts both
    // messages on screen, so submitMessage has nothing left to reconcile
    // — it only needs the ids (see its own assistant_message_id branch).
    return {
      reply: [],
      user_message_id: history[userIndex].id,
      assistant_message_id: assistant?.id ?? null,
    }
  }
}
