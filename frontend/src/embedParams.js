const searchParams = new URLSearchParams(window.location.search)

export function peekEmbedRequest() {
  const key = searchParams.get('embed')
  if (!key) return null
  return { key, projectId: searchParams.get('project'), sessionId: searchParams.get('session') }
}
