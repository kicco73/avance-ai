const searchParams = new URLSearchParams(window.location.search)

export function peekEmbedRequest() {
  const key = searchParams.get('embed')
  if (!key) return null
  return { key, projectId: searchParams.get('project'), sessionId: searchParams.get('session') }
}

export function buildEmbedUrl(key, projectId, sessionId) {
  const params = new URLSearchParams({ embed: key, project: projectId, session: sessionId })
  return `${window.location.origin}${window.location.pathname}?${params}`
}
