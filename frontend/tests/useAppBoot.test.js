import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, ref } from 'vue'

vi.mock('../src/api.js', () => ({
  getState: vi.fn(),
  getMe: vi.fn(),
  getProjects: vi.fn(),
  postRedeemInviteCode: vi.fn(),
  activateProject: vi.fn(),
  postAcceptTerms: vi.fn(),
  postLogout: vi.fn(),
  getPendingStatus: vi.fn(),
}))
vi.mock('../src/chatClient.js', () => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
}))
vi.mock('../src/errorStore.js', () => ({
  clearApiError: vi.fn(),
}))
vi.mock('../src/authStore.js', () => ({
  requireLogin: vi.fn(),
}))
vi.mock('../src/dialogStore.js', () => ({
  confirmDialog: vi.fn(),
}))
vi.mock('../src/shareLink.js', () => ({
  consumeInviteCode: vi.fn(() => null),
  peekInviteCode: vi.fn(() => null),
}))
vi.mock('../src/chatStore.js', () => ({
  setCapabilities: vi.fn(),
  setInputTokenBudgetPerTurn: vi.fn(),
  setTotalTokenBudgetPerSession: vi.fn(),
  handleStateChange: vi.fn(),
  loadMessages: vi.fn(),
  loadAiModels: vi.fn(),
}))

import { getState, getMe, getProjects, postRedeemInviteCode, activateProject, postAcceptTerms, postLogout, getPendingStatus } from '../src/api.js'
import { disconnect as disconnectChat } from '../src/chatClient.js'
import { clearApiError } from '../src/errorStore.js'
import { requireLogin } from '../src/authStore.js'
import { confirmDialog } from '../src/dialogStore.js'
import { consumeInviteCode, peekInviteCode } from '../src/shareLink.js'
import { setCapabilities, setInputTokenBudgetPerTurn, setTotalTokenBudgetPerSession, handleStateChange, loadMessages, loadAiModels } from '../src/chatStore.js'
import { useAppBoot } from '../src/composables/useAppBoot.js'

function mountComposable(setup) {
  let result
  const container = document.createElement('div')
  const app = createApp({ setup: () => { result = setup(); return () => null } })
  app.mount(container)
  return { result, unmount: () => app.unmount() }
}

describe('useAppBoot', () => {
  let unmount, currentUserProfile, currentUserRole, labelProjectName, liveChatProjectName,
    pushedView, chatOpen, showProfile, navDirection

  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    currentUserProfile = ref(null)
    currentUserRole = ref(null)
    labelProjectName = ref(null)
    liveChatProjectName = ref(null)
    pushedView = ref('chat')
    chatOpen = ref(false)
    showProfile = ref(true)
    navDirection = ref('back')
  })

  afterEach(() => {
    unmount?.()
    vi.useRealTimers()
  })

  function mount() {
    const mounted = mountComposable(() => useAppBoot(
      currentUserProfile, currentUserRole, labelProjectName, liveChatProjectName,
      pushedView, chatOpen, showProfile, navDirection
    ))
    unmount = mounted.unmount
    return mounted.result
  }

  describe('getActiveProjectId', () => {
    it('prefers the reported active project', async () => {
      getProjects.mockResolvedValue({ active: 'proj-a', projects: [{ name: 'proj-b' }] })
      const s = mount()
      expect(await s.getActiveProjectId()).toBe('proj-a')
    })

    it('falls back to the first project when none is active', async () => {
      getProjects.mockResolvedValue({ active: null, projects: [{ name: 'proj-b' }] })
      const s = mount()
      expect(await s.getActiveProjectId()).toBe('proj-b')
    })

    it('resolves null on a totally empty install or a fetch failure', async () => {
      getProjects.mockResolvedValue({ active: null, projects: [] })
      const s = mount()
      expect(await s.getActiveProjectId()).toBeNull()

      getProjects.mockRejectedValue(new Error('boom'))
      expect(await s.getActiveProjectId()).toBeNull()
    })
  })

  describe('startBootSequence -> runPingAttempt', () => {
    it('a ready backend resolves the landing view and flips bootStatus to ready', async () => {
      getState.mockResolvedValue({ talk_enabled: true, listen_enabled: true })
      getMe.mockResolvedValue({ role: 'user' })
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      expect(setCapabilities).toHaveBeenCalledWith({ talkAvailable: true, micAvailable: true })
      expect(setInputTokenBudgetPerTurn).toHaveBeenCalledWith(null)
      expect(setTotalTokenBudgetPerSession).toHaveBeenCalledWith(null)
      expect(handleStateChange).toHaveBeenCalled()
      expect(clearApiError).toHaveBeenCalled()
      expect(loadMessages).toHaveBeenCalled() // role === 'user'
      expect(loadAiModels).toHaveBeenCalled()
    })

    it('passes the backend-reported input token budget through, when present', async () => {
      getState.mockResolvedValue({ talk_enabled: true, listen_enabled: true, input_token_budget_per_turn: 8000 })
      getMe.mockResolvedValue({ role: 'user' })
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      expect(setInputTokenBudgetPerTurn).toHaveBeenCalledWith(8000)
    })

    it('passes the backend-reported total token budget through, when present', async () => {
      getState.mockResolvedValue({ talk_enabled: true, listen_enabled: true, total_token_budget_per_session: 200000 })
      getMe.mockResolvedValue({ role: 'user' })
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      expect(setTotalTokenBudgetPerSession).toHaveBeenCalledWith(200000)
    })

    it('resets the navigation stack before resolving who is landing where', async () => {
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'admin' })
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      expect(pushedView.value).toBeNull()
      expect(showProfile.value).toBe(false)
      expect(navDirection.value).toBe('forward')
      expect(loadMessages).not.toHaveBeenCalled() // admin, not user
    })

    it("a supervisor's landing view resolves their active project into labelProjectName", async () => {
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'supervisor' })
      getProjects.mockResolvedValue({ active: 'proj-x', projects: [] })
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      expect(labelProjectName.value).toBe('proj-x')
      expect(liveChatProjectName.value).toBeNull()
    })

    it('a 403 (pending registration) sets needsTerms instead of proceeding', async () => {
      getState.mockRejectedValue({ status: 403 })
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.needsTerms.value).toBe(true))

      expect(s.bootStatus.value).toBe('checking') // never touched
      expect(getMe).not.toHaveBeenCalled()
    })

    it('a pending pre-wired admin (no share link, no User row) resolves as invite-exempt', async () => {
      // Regression: an admin who erased their own data, then just logs
      // back in normally (no ?invite= link, see shareLink.js) — App.vue's
      // gate must still be able to route them to TermsView, not
      // InviteRequiredView's dead end. See AuthService.is_invite_exempt.
      getState.mockRejectedValue({ status: 403 })
      getPendingStatus.mockResolvedValue({ invite_exempt: true })
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.needsTerms.value).toBe(true))

      expect(s.inviteExempt.value).toBe(true)
    })

    it('a pending regular identity with no share link is not invite-exempt', async () => {
      getState.mockRejectedValue({ status: 403 })
      getPendingStatus.mockResolvedValue({ invite_exempt: false })
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.needsTerms.value).toBe(true))

      expect(s.inviteExempt.value).toBe(false)
    })

    it('fails closed (not exempt) if the pending-status check itself fails', async () => {
      getState.mockRejectedValue({ status: 403 })
      getPendingStatus.mockRejectedValue(new Error('boom'))
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.needsTerms.value).toBe(true))

      expect(s.inviteExempt.value).toBe(false)
    })

    it('a 401 just stops — apiFetch already triggered the login screen', async () => {
      getState.mockRejectedValue({ status: 401 })
      const s = mount()

      s.startBootSequence()
      await vi.runOnlyPendingTimersAsync()

      expect(s.bootStatus.value).toBe('checking')
    })

    it('a transient failure retries on an interval, then flips ready once the backend comes up', async () => {
      getState.mockRejectedValueOnce({}).mockRejectedValueOnce({}).mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'admin' })
      const s = mount()

      s.startBootSequence()
      await vi.advanceTimersByTimeAsync(0)
      expect(s.bootStatus.value).toBe('waiting')

      await vi.advanceTimersByTimeAsync(800)
      await vi.advanceTimersByTimeAsync(800)
      expect(s.bootStatus.value).toBe('ready')
    })

    it('gives up after the retry budget is exhausted', async () => {
      getState.mockRejectedValue({})
      const s = mount()

      s.startBootSequence()
      await vi.advanceTimersByTimeAsync(800 * 31)

      expect(s.bootStatus.value).toBe('failed')
    })

    it('calling startBootSequence again supersedes a still-pending retry loop', async () => {
      getState.mockRejectedValueOnce({}).mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'admin' })
      const s = mount()

      s.startBootSequence() // 1st sequence: will fail once, then schedule a retry
      await vi.advanceTimersByTimeAsync(0)
      expect(s.bootStatus.value).toBe('waiting')

      s.startBootSequence() // 2nd sequence supersedes it — bootStatus resets
      expect(s.bootStatus.value).toBe('checking')
      await vi.advanceTimersByTimeAsync(0)
      expect(s.bootStatus.value).toBe('ready')

      // The superseded 1st sequence's own scheduled retry, if it still fired,
      // must not have clobbered anything — advancing further stays 'ready'.
      await vi.advanceTimersByTimeAsync(800)
      expect(s.bootStatus.value).toBe('ready')
    })
  })

  describe('shared project landing (share-link QR flow)', () => {
    it('lands a plain user directly on the shared project instead of their previously active one', async () => {
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'user' })
      consumeInviteCode.mockReturnValueOnce('shared-id')
      postRedeemInviteCode.mockResolvedValue({ project_id: 'shared-project' })
      activateProject.mockResolvedValue({})
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      expect(postRedeemInviteCode).toHaveBeenCalledWith('shared-id')
      expect(activateProject).toHaveBeenCalledWith('shared-project')
      expect(liveChatProjectName.value).toBe('shared-project')
      expect(getProjects).not.toHaveBeenCalled() // never fell back to getActiveProjectId
    })

    it('pushes an admin straight into chat, on the shared project, and loads its messages', async () => {
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'admin' })
      consumeInviteCode.mockReturnValueOnce('shared-id')
      postRedeemInviteCode.mockResolvedValue({ project_id: 'shared-project' })
      activateProject.mockResolvedValue({})
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      // "Pushed straight into chat" is chatOpen, a separate flag from
      // pushedView (App.vue's own string enum for the *other* pushed
      // views — 'edit'/'label'/'manageUsers'/'appStore' — 'chat' was
      // never one of its values).
      expect(chatOpen.value).toBe(true)
      expect(liveChatProjectName.value).toBe('shared-project')
      expect(loadMessages).toHaveBeenCalled()
    })

    it("lands a supervisor's Label sessions view on the shared project", async () => {
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'supervisor' })
      consumeInviteCode.mockReturnValueOnce('shared-id')
      postRedeemInviteCode.mockResolvedValue({ project_id: 'shared-project' })
      activateProject.mockResolvedValue({})
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      expect(labelProjectName.value).toBe('shared-project')
      expect(getProjects).not.toHaveBeenCalled()
    })

    it('an admin with no shared id lands on Manage projects as usual (no push, no activation)', async () => {
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'admin' })
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      expect(activateProject).not.toHaveBeenCalled()
      expect(pushedView.value).toBeNull()
    })

    it('falls back to the normal landing when the shared id no longer resolves to a project', async () => {
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'user' })
      getProjects.mockResolvedValue({ active: 'proj-fallback', projects: [] })
      consumeInviteCode.mockReturnValueOnce('stale-id')
      postRedeemInviteCode.mockResolvedValue({ project_id: null })
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      expect(activateProject).not.toHaveBeenCalled()
      expect(liveChatProjectName.value).toBe('proj-fallback')
    })

    it('falls back to the normal landing when resolving the shared id fails', async () => {
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'user' })
      getProjects.mockResolvedValue({ active: 'proj-fallback', projects: [] })
      consumeInviteCode.mockReturnValueOnce('stale-id')
      postRedeemInviteCode.mockRejectedValue(new Error('boom'))
      const s = mount()

      s.startBootSequence()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      expect(liveChatProjectName.value).toBe('proj-fallback')
    })
  })

  it('resolveLandingView leaves currentUserRole alone if getMe() fails', async () => {
    getMe.mockRejectedValue(new Error('boom'))
    const s = mount()

    await s.resolveLandingView()

    expect(currentUserRole.value).toBeNull()
  })

  describe('handleLoggedIn', () => {
    it('restarts the boot sequence', async () => {
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'admin' })
      const s = mount()

      s.handleLoggedIn()

      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))
    })
  })

  describe('handleTermsAccept', () => {
    it('accepts, clears needsTerms, and restarts booting on success', async () => {
      postAcceptTerms.mockResolvedValue()
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'admin' })
      const s = mount()
      s.needsTerms.value = true

      await s.handleTermsAccept()
      await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))

      expect(s.needsTerms.value).toBe(false)
    })

    it('stays on the terms screen if accepting fails', async () => {
      postAcceptTerms.mockRejectedValue(new Error('boom'))
      const s = mount()
      s.needsTerms.value = true

      await s.handleTermsAccept()

      expect(s.needsTerms.value).toBe(true)
    })

    it('surfaces the backend-reported reason (e.g. an expired/maxed invite) in termsError', async () => {
      const err = new Error('This invite link has expired.')
      err.detail = ''
      postAcceptTerms.mockRejectedValue(err)
      const s = mount()
      s.needsTerms.value = true

      await s.handleTermsAccept()

      expect(s.termsError.value).toBe('This invite link has expired.')
      expect(s.needsTerms.value).toBe(true)
    })

    it('clears any previous termsError at the start of a fresh attempt', async () => {
      postAcceptTerms.mockRejectedValueOnce(new Error('nope')).mockResolvedValueOnce()
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'admin' })
      const s = mount()
      s.needsTerms.value = true

      await s.handleTermsAccept()
      expect(s.termsError.value).toBe('nope')

      await s.handleTermsAccept()
      expect(s.termsError.value).toBe('')
    })

    it('sends the peeked (not consumed) shared project id — registration is invite-only', async () => {
      postAcceptTerms.mockResolvedValue()
      peekInviteCode.mockReturnValueOnce('invite-id')
      const s = mount()
      s.needsTerms.value = true

      await s.handleTermsAccept()

      expect(postAcceptTerms).toHaveBeenCalledWith('invite-id')
      expect(consumeInviteCode).not.toHaveBeenCalled() // still there for the later landing resolution
    })
  })

  describe('handleTermsReject', () => {
    it('logs out cleanly without ever having registered', async () => {
      postLogout.mockResolvedValue()
      const s = mount()
      s.needsTerms.value = true

      await s.handleTermsReject()

      expect(disconnectChat).toHaveBeenCalled()
      expect(s.needsTerms.value).toBe(false)
      expect(requireLogin).toHaveBeenCalled()
    })

    it('clears inviteExempt so a later identity in the same tab never inherits it', async () => {
      postLogout.mockResolvedValue()
      const s = mount()
      s.needsTerms.value = true
      s.inviteExempt.value = true

      await s.handleTermsReject()

      expect(s.inviteExempt.value).toBe(false)
    })
  })

  describe('handleLogout', () => {
    it('does nothing without confirmation', async () => {
      confirmDialog.mockResolvedValue(false)
      const s = mount()

      await s.handleLogout()

      expect(postLogout).not.toHaveBeenCalled()
      expect(requireLogin).not.toHaveBeenCalled()
    })

    it('logs out on confirmation, even if the server call fails', async () => {
      confirmDialog.mockResolvedValue(true)
      postLogout.mockRejectedValue(new Error('boom'))
      const s = mount()

      await s.handleLogout()

      expect(disconnectChat).toHaveBeenCalled()
      expect(requireLogin).toHaveBeenCalled()
    })
  })
})
