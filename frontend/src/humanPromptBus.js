// The frontend half of HumanTalker's manual-testing seam (see
// talker.human_talker and chat.ws_human_relay.WsHumanRelay): a
// human_prompt frame means some session's next turn is waiting on this
// tab (or one of this account's other open tabs — see MAX_CONNECTIONS_PER_
// ADMIN) to answer as the human. There is nowhere to navigate to first —
// the reply travels back over the same socket, correlated by prompt_id —
// so the toast itself (see HumanPromptToasts.vue) carries a reply box, and
// this module just wires the socket frame to the store and back.
//
// Registers itself as soon as this module is imported (mirrors
// chatClient.js's own module-level subscribe pattern) — HumanPromptToasts.vue
// imports it purely for this side effect.
import { chatChannel } from './chatChannel.js'
import { addHumanPrompt, removeHumanPrompt } from './humanPromptStore.js'

chatChannel.subscribe('human_prompt', (frame) => {
  addHumanPrompt({
    promptId: frame.prompt_id,
    sessionId: frame.session_id,
    sessionType: frame.session_type,
    projectId: frame.project_id,
    text: frame.text
  })
})

export function sendHumanReply(promptId, text) {
  chatChannel.send({ type: 'human_reply', prompt_id: promptId, text })
  removeHumanPrompt(promptId)
}

export function dismissHumanPrompt(promptId) {
  removeHumanPrompt(promptId)
}
