// Which sessions this tab is currently showing.
//
// A human_takeover frame for a session already on screen is not news (see
// humanTakeoverBus.js). The live chat is one such screen; the editor's
// embedded test chat is another, and it belongs to a skill — so rather
// than the core importing that store to ask, every chat store registers
// the session it is showing here. A leaf, so a skill can reach back into
// it without closing an import cycle.
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
