// A human_prompt frame means some session's current turn is waiting on
// this tab to answer as the human. Registering for it is what says this
// tab is the one answering: the server sends the frame to whoever asked
// for it and to nobody else (see system/bus_channel.py's own
// CLIENT_REGISTRABLE), so this is called by HumanOperatorChatView.vue
// while it is open — not at import time, by every tab.
//
// A prompt that fired before the view opened is delivered on registering,
// so what lands here is whatever that session still has to answer.
import { busChannel } from './busChannel.js'
import { addHumanPrompt } from './humanPromptStore.js'

export function listenForHumanPrompts() {
  return busChannel.subscribe('human_prompt', (frame) => {
    addHumanPrompt({
      promptId: frame.prompt_id,
      sessionId: frame.session_id,
      sessionType: frame.session_type,
      projectId: frame.project_id,
      text: frame.text
    })
  })
}
