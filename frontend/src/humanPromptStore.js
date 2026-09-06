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

// HumanOperatorChatView.vue's own lookup: the prompt (if any) its
// session's input box is currently meant to answer — at most one at a
// time per session, since HumanTalker.talk() awaits one reply before
// asking again (see chat/ws_notifications.py's _pending_human_replies).
export function getHumanPromptForSession(sessionId) {
  return humanPrompts.value.find((p) => p.sessionId === sessionId) ?? null
}
