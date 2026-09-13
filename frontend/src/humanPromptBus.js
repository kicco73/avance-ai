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
