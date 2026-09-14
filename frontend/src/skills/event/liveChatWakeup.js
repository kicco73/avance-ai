import { busChannel } from '../../busChannel.js'

class LiveChatWakeup {

  constructor() {
    this._watching = new Set()
  }

  liveChatOpened(chat) {
    if (this._watching.has(chat)) return
    this._watching.add(chat)
    busChannel.subscribe('ui.notification', ({ project_name, state }) => {
      if (state && project_name === chat.currentProjectId.value) chat.handleStateChange(state)
    })
  }
}

export const liveChatWakeup = new LiveChatWakeup()
