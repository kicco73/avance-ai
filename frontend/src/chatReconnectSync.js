import { getSessionState } from './api.js'
import { liveChatChannel } from './liveChatChannel.js'
import { busChannel } from './busChannel.js'

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
    busChannel.onConnectionState((state, { reconnected }) => {
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
      [history, sessionState] = await Promise.all([liveChatChannel().getMessages(sessionId), getSessionState(sessionId)])
    } catch {
      return // already surfaced via apiFetch
    }
    if (chat.currentSessionId.value !== sessionId) return

    const unsent = this.neverArrived(chat.messages.value, history)
    chat.abandonOpenReplies()
    chat.messages.value = [...history.map((row) => chat.toStoreMessage(row)), ...unsent]
    chat.state.value = sessionState
  }

  // What the person said that the server has no record of: their bubble
  // is carried over the reload, marked failed, so it can be sent again.
  // Read off the reloaded rows rather than off what any turn reported —
  // the rows are the only thing that survived the drop.
  neverArrived(local, history) {
    return local
      .filter((m) => m.role === 'user' && m.messageId == null)
      .filter((m) => !history.some((row) => row.role === 'user' && row.content === m.content))
      .map((m) => ({ ...m, failed: true }))
  }
}
