import { ref } from 'vue'

export const humanPrompts = ref([])

export function addHumanPrompt(prompt) {
  humanPrompts.value.push(prompt)
}

export function removeHumanPrompt(promptId) {
  const idx = humanPrompts.value.findIndex((p) => p.promptId === promptId)
  if (idx !== -1) humanPrompts.value.splice(idx, 1)
}

export function getHumanPromptForSession(sessionId) {
  return humanPrompts.value.find((p) => p.sessionId === sessionId) ?? null
}
