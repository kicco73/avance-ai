function storageKey(kind, sessionId) {
  return `avance.backgroundAudio.${kind}.${sessionId}`
}

export function rememberBackgroundAudio(kind, sessionId, url) {
  try {
    localStorage.setItem(storageKey(kind, sessionId), url)
  } catch {
  }
}

export function recallBackgroundAudio(kind, sessionId) {
  try {
    return localStorage.getItem(storageKey(kind, sessionId))
  } catch {
    return null
  }
}

export function forgetBackgroundAudio(kind, sessionId) {
  try {
    localStorage.removeItem(storageKey(kind, sessionId))
  } catch {
  }
}
