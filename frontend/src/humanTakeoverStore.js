import { ref } from 'vue'

// Pending human_takeover pushes (see chat/ws_notifications.py's
// send_human_takeover, fired by actuator.switch_to_human) — one of this
// account's other open tabs (see MAX_CONNECTIONS_PER_ADMIN) needs to open
// a session and answer as the human. Unlike humanPromptStore.js's own
// per-turn prompts, these never resolve anything by themselves — they're
// just a link, dismissed once followed or closed.
export const humanTakeovers = ref([])

// Set by openHumanTakeover() below, watched by App.vue to actually push
// the HumanOperatorChatView.vue instance — the store has no App-level
// navigation of its own, so this is the one thing it hands upward.
export const requestedOperatorSession = ref(null)

let nextId = 0

export function addHumanTakeover(sessionId, projectId) {
  humanTakeovers.value.push({ id: ++nextId, sessionId, projectId })
}

export function dismissHumanTakeover(id) {
  const idx = humanTakeovers.value.findIndex((t) => t.id === id)
  if (idx !== -1) humanTakeovers.value.splice(idx, 1)
}

export function openHumanTakeover(id, sessionId, projectId) {
  dismissHumanTakeover(id)
  requestedOperatorSession.value = { sessionId, projectId }
}

export function clearRequestedOperatorSession() {
  requestedOperatorSession.value = null
}
