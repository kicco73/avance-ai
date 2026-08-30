// Captures a `?project=<id>` query param at page load — the id half of
// a project's own share link (see ShareProjectDialog.vue, which builds
// it) — and strips it from the address bar immediately, so refreshing
// or re-sharing the same tab afterward doesn't re-trigger it.
// consumeSharedProjectId() is the only way anything reads it, and only
// once: useAppBoot.js's resolveLandingView calls it right after login
// resolves (the first point, whether already logged in or freshly so,
// where it's safe to activate/land on the referenced project).
const params = new URLSearchParams(window.location.search)
let sharedProjectId = params.get('project') || null

if (sharedProjectId) {
  params.delete('project')
  const rest = params.toString()
  history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash)
}

export function consumeSharedProjectId() {
  const id = sharedProjectId
  sharedProjectId = null
  return id
}

// The inverse of the above — ShareProjectDialog.vue's own link builder,
// kept next to the param name it must match.
export function buildShareUrl(projectId) {
  return `${window.location.origin}${window.location.pathname}?project=${encodeURIComponent(projectId)}`
}
