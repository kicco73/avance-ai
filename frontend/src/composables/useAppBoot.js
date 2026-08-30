import { ref } from 'vue'
import { getState, getMe, getProjects, getProjectByShareId, activateProject, postAcceptTerms, postLogout } from '../api.js'
import { disconnect as disconnectChat } from '../chatClient.js'
import { clearApiError } from '../errorStore.js'
import { requireLogin } from '../authStore.js'
import { confirmDialog } from '../dialogStore.js'
import { consumeSharedProjectId } from '../shareLink.js'
import { setCapabilities, setInputTokenBudgetPerTurn, setTotalTokenBudgetPerSession, handleStateChange, loadMessages, loadAiModels } from '../chatStore.js'

// App.vue's own boot sequence: the backend-readiness ping loop, resolving
// which view a freshly-booted session lands on, and every navigate-away
// action (login/logout/terms accept-reject) that re-enters or exits this
// sequence. `currentUserProfile`/`currentUserRole`/`labelProjectName`/
// `liveChatProjectName`/`pushedView`/`showProfile`/`navDirection` are
// App.vue's own — this composable only reads/resolves them.
export function useAppBoot(
  currentUserProfile, currentUserRole, labelProjectName, liveChatProjectName,
  pushedView, showProfile, navDirection
) {
  // Initial-boot backend readiness gate — entirely separate from the shared
  // error store (which is for runtime errors on an already-running app). 'checking': the
  // very first, invisible ping attempt (no splash yet, so a backend that's
  // already up never flashes one). 'waiting': the first attempt failed,
  // retrying on an interval with the splash visible. 'ready': normal app UI.
  // 'failed': retry budget exhausted, explicit error + manual "Retry".
  const bootStatus = ref('checking')
  // True for a session that authenticated but has no User row — either a
  // brand-new identity that never finished registration, or one whose row
  // was deleted after the cookie was issued (see auth_service.py's own
  // verify_token: both resolve to role=None, indistinguishable from here).
  // TermsView.vue's Accept calls complete_registration(), which creates the
  // row either way — this is the only path that recovers either case; a
  // plain re-login just reissues the same role=None token. Takes over the
  // whole screen the same way needsLogin does, ahead of bootStatus.
  const needsTerms = ref(false)

  const PING_INTERVAL_MS = 800
  const PING_TIMEOUT_MS = 3000
  const MAX_PING_ATTEMPTS = 30

  // Boot-ping bookkeeping. `bootSequenceToken` is bumped by startBootSequence()
  // so a stale scheduled retry from a previous sequence (e.g. right after the
  // user clicks "Retry") can recognize it's been superseded and no-op instead
  // of racing the fresh one.
  let pingAttempts = 0
  let pingTimeoutHandle = null
  let bootSequenceToken = 0

  // One ping attempt, bounded by an explicit timeout — plain fetch() never
  // times out on its own against a hung connection, and "timeout" is one of
  // the failure modes this boot check needs to treat the same as "not ready
  // yet". On success, reuses the result directly as the app's current state
  // (GET /api/state IS the readiness check — nothing else to fetch for it).
  // 'ready': backend is up and this session is a fully registered user.
  // 'pending': backend is up, but this session authenticated without a
  // matching User row — GET /api/state requires role="user", which a
  // pending (role=None) identity's 403 is the only way to reach here (see
  // auth_service.py's own verify_token). Never "still booting": retrying
  // with the same cookie always gets the same 403 back. 'retry': anything
  // else (backend still starting, network hiccup, timeout).
  async function pingBackend() {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), PING_TIMEOUT_MS)
    try {
      const newState = await getState(controller.signal)
      setCapabilities({ talkAvailable: newState.talk_enabled ?? true, micAvailable: newState.listen_enabled ?? true })
      setInputTokenBudgetPerTurn(newState.input_token_budget_per_turn ?? null)
      setTotalTokenBudgetPerSession(newState.total_token_budget_per_session ?? null)
      handleStateChange(newState)
      return 'ready'
    } catch (err) {
      if (err.status === 403) return 'pending'
      // apiFetch already called requireLogin() for this — LoginView is
      // showing. Nothing to retry until the user logs back in, which
      // restarts this whole sequence itself (see handleLoggedIn).
      if (err.status === 401) return 'unauthorized'
      return 'retry'
    } finally {
      clearTimeout(timeout)
    }
  }

  function bootSucceeded() {
    bootStatus.value = 'ready'
    // Clears any error left over from a failed boot-ping retry — that
    // retry loop is invisible UI (see pingBackend), but it goes through the
    // same apiFetch as everything else, so a stale message could otherwise
    // still be sitting in the shared store the moment the chat UI mounts.
    clearApiError()
    // loadMessages() is what actually creates/resolves the live session
    // (see chatStoreFactory.js's ensureSession) — only a plain user's chat
    // landing needs one at boot; an admin (Manage projects, permanently
    // mounted) or supervisor (Label sessions, their whole app) shouldn't
    // spin up a live session nobody's about to see. ChatWindow.vue only
    // mounts for them at all once they actually push into it (see
    // handleManageProjectsChat -> handleProjectSwitch's own loadMessages()).
    // The pushedView === 'chat' case covers an admin landing straight into
    // chat via a share link (see resolveLandingView below) — same reasoning,
    // a live session is actually about to show for them too in that case.
    if (currentUserRole.value === 'user' || pushedView.value === 'chat') {
      loadMessages()
    }
    loadAiModels()
    // No proactive chat-socket connect here: chatClient.js connects lazily
    // on the first sendMessage() call, and the opening message (if any) is
    // already covered by loadMessages() above — it's persisted server-side
    // by the time the backend finishes booting, regardless of transport.
  }

  // The one shared way to resolve "the active project" for a caller with no
  // specific project of its own in hand (resolveLandingView's supervisor
  // case and Settings menu's Label sessions/Edit projects below — nothing
  // more specific to go on in any of them). Never stored anywhere, never a
  // fallback for an explicit project: resolved fresh, straight off the
  // backend, every single time. A Manage projects row click, Label
  // Sessions' own ProjectsMenu pick, etc. always carry their own project
  // name already and have no reason to call this at all.
  async function getActiveProjectName() {
    try {
      const res = await getProjects()
      // No active project set at all (a fresh install, or one manually
      // cleared) — the first project in the list beats returning nothing.
      return res.active ?? res.projects[0]?.name ?? null
    } catch {
      // already surfaced via apiFetch
      return null
    }
  }

  // The landing-time half of the "share project" QR/link flow (see
  // shareLink.js and ShareProjectDialog.vue for the other half — capturing
  // the id and rendering the QR that carries it). Consumes the id captured
  // at page load, resolves it to a project name, and activates it
  // server-side so whichever landing view resolveLandingView picks below
  // (chat for a user, Manage projects -> chat for an admin, Label sessions
  // for a supervisor) opens on that project instead of the one already
  // active. Returns null — and leaves the previously active project alone
  // — whenever there was no shared id, or it no longer resolves to a real
  // project (deleted, or its id changed since the link was generated).
  async function activateSharedProject() {
    const projectId = consumeSharedProjectId()
    if (!projectId) return null
    try {
      const { project_name: projectName } = await getProjectByShareId(projectId)
      if (!projectName) return null
      await activateProject(projectName)
      return projectName
    } catch {
      return null // already surfaced via apiFetch; falls back to the normal landing
    }
  }

  // Resolved once per boot, before bootStatus ever flips to 'ready' — the
  // landing view has to be right from the very first render, not settled a
  // moment later once some async fetch resolves (that would flash the
  // chat-live default first for every supervisor/admin). currentUserRole
  // alone drives which of the 3 role branches the template renders; a
  // supervisor additionally needs their active project up front, since
  // LabelProjectView *is* their whole app, not something pushed later.
  async function resolveLandingView() {
    // A logout doesn't reload the page — it's the same running app instance
    // just re-showing LoginView — so anything left over from a previous
    // session (mid-erase-account they were on Profile, admin had something
    // pushed, ...) would otherwise still be sitting in these refs the next
    // time boot succeeds, landing the new session somewhere it never
    // actually navigated to itself.
    pushedView.value = null
    showProfile.value = false
    navDirection.value = 'forward'
    try {
      currentUserProfile.value = await getMe()
      currentUserRole.value = currentUserProfile.value?.role ?? null
    } catch {
      return // already surfaced via apiFetch; falls back to the chat-live default
    }
    const sharedProjectName = await activateSharedProject()
    if (currentUserRole.value === 'supervisor') {
      labelProjectName.value = sharedProjectName ?? await getActiveProjectName()
    }
    if (currentUserRole.value === 'user') {
      liveChatProjectName.value = sharedProjectName ?? await getActiveProjectName()
    }
    if (currentUserRole.value === 'admin' && sharedProjectName) {
      liveChatProjectName.value = sharedProjectName
      pushedView.value = 'chat'
    }
  }

  async function runPingAttempt(token) {
    if (token !== bootSequenceToken) return // superseded by a newer sequence
    pingAttempts++
    const result = await pingBackend()
    if (token !== bootSequenceToken) return
    if (result === 'unauthorized') return
    if (result === 'ready') {
      await resolveLandingView()
      if (token !== bootSequenceToken) return
      bootSucceeded()
      return
    }
    if (result === 'pending') {
      needsTerms.value = true
      return
    }
    if (pingAttempts >= MAX_PING_ATTEMPTS) {
      bootStatus.value = 'failed'
      return
    }
    bootStatus.value = 'waiting'
    pingTimeoutHandle = setTimeout(() => runPingAttempt(token), PING_INTERVAL_MS)
  }

  // Entry point for both the initial mount and the splash's manual "Retry" —
  // restarts the exact same cycle: one immediate, invisible attempt, then
  // (only if that one fails) the visible retry loop.
  function startBootSequence() {
    bootSequenceToken++
    pingAttempts = 0
    if (pingTimeoutHandle) {
      clearTimeout(pingTimeoutHandle)
      pingTimeoutHandle = null
    }
    bootStatus.value = 'checking'
    runPingAttempt(bootSequenceToken)
  }

  // LoginView.vue's own 'logged-in' — the session cookie is set, so the
  // exact same startup path a fresh page load takes now succeeds instead
  // of 401ing.
  function handleLoggedIn() {
    startBootSequence()
  }

  // TermsView.vue's Accept — creates the User row (or recreates a deleted
  // one, see needsTerms's own comment), then resumes booting exactly like
  // a fresh login would: the same cookie now resolves as a registered user.
  async function handleTermsAccept() {
    try {
      await postAcceptTerms()
    } catch {
      // already surfaced via apiFetch; stays on TermsView so it can be retried
      return
    }
    needsTerms.value = false
    startBootSequence()
  }

  // TermsView.vue's Reject — same clean logout as handleLogout, but no
  // User row was ever created (or recreated), so this leaves no trace of
  // the attempt.
  async function handleTermsReject() {
    try {
      await postLogout()
    } catch {
      // already surfaced via apiFetch
    }
    disconnectChat()
    needsTerms.value = false
    requireLogin()
  }

  async function handleLogout() {
    const ok = await confirmDialog({ title: 'Log out', body: 'Log out of Avance?', okLabel: 'Log out' })
    if (!ok) return
    try {
      await postLogout()
    } catch {
      // already surfaced via apiFetch
    }
    disconnectChat()
    requireLogin()
  }

  return {
    bootStatus, needsTerms,
    getActiveProjectName, resolveLandingView, startBootSequence,
    handleLoggedIn, handleTermsAccept, handleTermsReject, handleLogout,
  }
}
