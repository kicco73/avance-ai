import { busChannel } from './busChannel.js'

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
