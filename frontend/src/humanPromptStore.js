import { ref } from 'vue'

// Pending human_prompt frames (see chat/ws_notifications.py's
// send_human_prompt and talker.human_talker.HumanTalker) — a person's own
// browser tab is being asked to answer a turn instead of the model. Unlike
// toastStore.js's own notify() toasts, these never auto-dismiss: the turn
// on the other end is genuinely blocked waiting for a reply.
export const humanPrompts = ref([])

export function addHumanPrompt(prompt) {
  humanPrompts.value.push(prompt)
}

export function removeHumanPrompt(promptId) {
  const idx = humanPrompts.value.findIndex((p) => p.promptId === promptId)
  if (idx !== -1) humanPrompts.value.splice(idx, 1)
}
