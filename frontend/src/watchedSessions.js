const watched = new Set()

export function watchSession(sessionId, previousSessionId = null) {
  if (previousSessionId != null) watched.delete(previousSessionId)
  if (sessionId != null) watched.add(sessionId)
}

export function unwatchSession(sessionId) {
  watched.delete(sessionId)
}

export function isWatched(sessionId) {
  return sessionId != null && watched.has(sessionId)
}
