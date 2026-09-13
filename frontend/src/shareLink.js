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

export function peekInviteCode() {
  return inviteCode
}

export function buildInviteUrl(code) {
  return `${window.location.origin}${window.location.pathname}?invite=${encodeURIComponent(code)}`
}
