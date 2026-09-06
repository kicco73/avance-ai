// A human_prompt frame means some session's current turn is waiting on
// this tab (or one of this account's other open tabs) to answer as the
// human. Registers itself as soon as this module is imported — App.vue
// imports it purely for this side effect, so the subscription is always
// live regardless of which page is open; HumanOperatorChatView.vue reads
// what lands here through humanPromptStore.js and replies directly over
// chatChannel itself.
import { chatChannel } from './chatChannel.js'
import { addHumanPrompt } from './humanPromptStore.js'

chatChannel.subscribe('human_prompt', (frame) => {
  addHumanPrompt({
    promptId: frame.prompt_id,
    sessionId: frame.session_id,
    sessionType: frame.session_type,
    projectId: frame.project_id,
    text: frame.text
  })
})
