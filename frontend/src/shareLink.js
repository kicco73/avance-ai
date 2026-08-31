// Captures a `?invite=<code>` query param at page load — the code half
// of a project's own invite link (see ShareProjectDialog.vue, which
// gets it from POST /api/projects/{name}/invites and builds the link
// with buildInviteUrl below) — and strips it from the address bar
// immediately, so refreshing or re-sharing the same tab afterward
// doesn't re-trigger it. consumeInviteCode() is the only way anything
// spends it, and only once: useAppBoot.js's resolveLandingView calls it
// right after login resolves (the first point, whether already logged
// in or freshly so, where it's safe to activate/land on the referenced
// project).
const params = new URLSearchParams(window.location.search)
let inviteCode = params.get('invite') || null

if (inviteCode) {
  params.delete('invite')
  const rest = params.toString()
  history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash)
}

export function consumeInviteCode() {
  const code = inviteCode
  inviteCode = null
  return code
}

// Non-destructive read of the same code — for a check that must run
// *before* the point consumeInviteCode() itself is called (see
// useAppBoot.js's handleTermsAccept and App.vue's own registration
// gate), without spending the one-time consumption
// activateInvitedProject() still needs afterward.
export function peekInviteCode() {
  return inviteCode
}

// The inverse of the above — ShareProjectDialog.vue's own link builder,
// kept next to the param name it must match.
export function buildInviteUrl(code) {
  return `${window.location.origin}${window.location.pathname}?invite=${encodeURIComponent(code)}`
}
