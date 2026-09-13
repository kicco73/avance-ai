import { busChannel } from './busChannel.js'

// A dropped socket loses whatever the server was still sending, and a
// fresh one knows nothing about which conversation this store is
// showing. Both are answered by the same sentence: enter the
// conversation again. What comes back rebuilds it — which conversation
// it is, what was said, what it offers — so there is nothing here to
// reconcile by hand. One of these per chat store; `chat` is that store's
// own view of the session it is showing.
export class ChatReconnectSync {
  constructor(chat) {
    this._chat = chat
  }

  register() {
    busChannel.onConnectionState((state) => {
      if (state !== 'open') return
      this._chat.abandonOpenReplies()
      this._chat.reenter()
    })
  }
}
