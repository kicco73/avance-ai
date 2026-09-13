import { ref } from 'vue'

export const humanTakeovers = ref([])

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
