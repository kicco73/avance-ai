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
  disconnect: vi.fn(), getConnectionState: vi.fn(() => 'open'), onConnectionState: vi.fn(() => () => {}), resolvePendingTurnsAfterReload: vi.fn() }))
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

  async function bootAs(role, state = {}) {
    getState.mockResolvedValue(state)
    getMe.mockResolvedValue({ role })
    const s = mount()
    s.startBootSequence()
    await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))
    return s
  }

  function sharedInvite(projectId = 'shared-project') {
    consumeInviteCode.mockReturnValueOnce('shared-id')
    postRedeemInviteCode.mockResolvedValue({ project_id: projectId })
    activateProject.mockResolvedValue({})
  }

  async function bootPending(pendingStatus) {
    getState.mockRejectedValue({ status: 403 })
    if (pendingStatus instanceof Error) getPendingStatus.mockRejectedValue(pendingStatus)
    else getPendingStatus.mockResolvedValue(pendingStatus)
    const s = mount()
    s.startBootSequence()
    await vi.waitFor(() => expect(s.needsTerms.value).toBe(true))
    return s
  }

  it('getActiveProjectId prefers the reported active project, falls back to the first, and resolves null on an empty install or a failure', async () => {
    getProjects.mockResolvedValue({ active: 'proj-a', projects: [{ name: 'proj-b' }] })
    const s = mount()
    expect(await s.getActiveProjectId()).toBe('proj-a')

    getProjects.mockResolvedValue({ active: null, projects: [{ name: 'proj-b' }] })
    expect(await s.getActiveProjectId()).toBe('proj-b')

    getProjects.mockResolvedValue({ active: null, projects: [] })
    expect(await s.getActiveProjectId()).toBeNull()

    getProjects.mockRejectedValue(new Error('boom'))
    expect(await s.getActiveProjectId()).toBeNull()
  })

  describe('startBootSequence -> runPingAttempt', () => {
    it('a ready backend publishes the reported capabilities and budgets, then lands a user in chat', async () => {
      await bootAs('user', {
        talk_enabled: true, listen_enabled: true, input_token_budget_per_turn: 8000, total_token_budget_per_session: 200000,
      })

      expect(setCapabilities).toHaveBeenCalledWith({ talkAvailable: true, micAvailable: true })
      expect(setInputTokenBudgetPerTurn).toHaveBeenCalledWith(8000)
      expect(setTotalTokenBudgetPerSession).toHaveBeenCalledWith(200000)
      expect(handleStateChange).toHaveBeenCalled()
      expect(clearApiError).toHaveBeenCalled()
      expect(loadMessages).toHaveBeenCalled()
      expect(loadAiModels).toHaveBeenCalled()
    })

    it('an absent budget is published as null, and the navigation stack is reset before resolving where an admin lands', async () => {
      const s = await bootAs('admin', { talk_enabled: true, listen_enabled: true })

      expect(setInputTokenBudgetPerTurn).toHaveBeenCalledWith(null)
      expect(setTotalTokenBudgetPerSession).toHaveBeenCalledWith(null)
      expect(pushedView.value).toBeNull()
      expect(showProfile.value).toBe(false)
      expect(navDirection.value).toBe('forward')
      expect(loadMessages).not.toHaveBeenCalled() // admin, not user
      expect(s.bootStatus.value).toBe('ready')
    })

    it("a supervisor's landing view resolves their active project into labelProjectName", async () => {
      getProjects.mockResolvedValue({ active: 'proj-x', projects: [] })

      await bootAs('supervisor')

      expect(labelProjectName.value).toBe('proj-x')
      expect(liveChatProjectName.value).toBeNull()
    })

    it('a 403 sets needsTerms without booting, reporting invite exemption only when the pending-status check says so', async () => {
      const pending = await bootPending({ invite_exempt: true })
      expect(pending.bootStatus.value).toBe('checking') // never touched
      expect(getMe).not.toHaveBeenCalled()
      // Regression: an admin who erased their own data, then just logs
      // back in normally (no ?invite= link, see shareLink.js) — App.vue's
      // gate must still route them to TermsView, not InviteRequiredView's
      // dead end. See AuthService.is_invite_exempt.
      expect(pending.inviteExempt.value).toBe(true)

      expect((await bootPending({ invite_exempt: false })).inviteExempt.value).toBe(false)
      // Fails closed if the check itself fails.
      expect((await bootPending(new Error('boom'))).inviteExempt.value).toBe(false)
    })

    it('a 401 just stops — apiFetch already triggered the login screen', async () => {
      getState.mockRejectedValue({ status: 401 })
      const s = mount()

      s.startBootSequence()
      await vi.runOnlyPendingTimersAsync()

      expect(s.bootStatus.value).toBe('checking')
    })

    it('a transient failure retries on an interval until the backend comes up, giving up once the budget is exhausted', async () => {
      getState.mockRejectedValueOnce({}).mockRejectedValueOnce({}).mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'admin' })
      const s = mount()

      s.startBootSequence()
      await vi.advanceTimersByTimeAsync(0)
      expect(s.bootStatus.value).toBe('waiting')

      await vi.advanceTimersByTimeAsync(800)
      await vi.advanceTimersByTimeAsync(800)
      expect(s.bootStatus.value).toBe('ready')
      s.unmount?.()

      getState.mockRejectedValue({})
      const failing = mount()
      failing.startBootSequence()
      await vi.advanceTimersByTimeAsync(800 * 31)
      expect(failing.bootStatus.value).toBe('failed')
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
    it('lands every role on the shared project instead of their previously active one, never falling back to getActiveProjectId', async () => {
      sharedInvite()
      await bootAs('user')
      expect(postRedeemInviteCode).toHaveBeenCalledWith('shared-id')
      expect(activateProject).toHaveBeenCalledWith('shared-project')
      expect(liveChatProjectName.value).toBe('shared-project')
      expect(getProjects).not.toHaveBeenCalled()
      unmount?.()

      sharedInvite()
      await bootAs('supervisor')
      expect(labelProjectName.value).toBe('shared-project')
      expect(getProjects).not.toHaveBeenCalled()
    })

    it('pushes an admin straight into chat on the shared project and loads its messages', async () => {
      sharedInvite()

      await bootAs('admin')

      // "Pushed straight into chat" is chatOpen, a separate flag from
      // pushedView (App.vue's own string enum for the *other* pushed
      // views — 'edit'/'label'/'manageUsers'/'appStore' — 'chat' was
      // never one of its values).
      expect(chatOpen.value).toBe(true)
      expect(liveChatProjectName.value).toBe('shared-project')
      expect(loadMessages).toHaveBeenCalled()
    })

    it('an admin with no shared id lands on Manage projects as usual, with no push and no activation', async () => {
      await bootAs('admin')

      expect(activateProject).not.toHaveBeenCalled()
      expect(pushedView.value).toBeNull()
    })

    it('falls back to the normal landing when the shared id no longer resolves or resolving it fails', async () => {
      getProjects.mockResolvedValue({ active: 'proj-fallback', projects: [] })
      consumeInviteCode.mockReturnValueOnce('stale-id')
      postRedeemInviteCode.mockResolvedValue({ project_id: null })

      await bootAs('user')
      expect(activateProject).not.toHaveBeenCalled()
      expect(liveChatProjectName.value).toBe('proj-fallback')
      unmount?.()

      liveChatProjectName.value = null
      consumeInviteCode.mockReturnValueOnce('stale-id')
      postRedeemInviteCode.mockRejectedValue(new Error('boom'))

      await bootAs('user')
      expect(liveChatProjectName.value).toBe('proj-fallback')
    })
  })

  it('resolveLandingView leaves currentUserRole alone if getMe() fails', async () => {
    getMe.mockRejectedValue(new Error('boom'))
    const s = mount()

    await s.resolveLandingView()

    expect(currentUserRole.value).toBeNull()
  })

  it('handleLoggedIn restarts the boot sequence', async () => {
    getState.mockResolvedValue({})
    getMe.mockResolvedValue({ role: 'admin' })
    const s = mount()

    s.handleLoggedIn()

    await vi.waitFor(() => expect(s.bootStatus.value).toBe('ready'))
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

    it('sends the peeked (not consumed) shared project id — registration is invite-only', async () => {
      postAcceptTerms.mockResolvedValue()
      peekInviteCode.mockReturnValueOnce('invite-id')
      const s = mount()
      s.needsTerms.value = true

      await s.handleTermsAccept()

      expect(postAcceptTerms).toHaveBeenCalledWith('invite-id')
      // Still there for the later landing resolution.
      expect(consumeInviteCode).not.toHaveBeenCalled()
    })

    it('stays on the terms screen when accepting fails, surfacing the backend reason and clearing it on the next attempt', async () => {
      const expired = new Error('This invite link has expired.')
      expired.detail = ''
      postAcceptTerms.mockRejectedValue(expired)
      const s = mount()
      s.needsTerms.value = true

      await s.handleTermsAccept()

      expect(s.needsTerms.value).toBe(true)
      expect(s.termsError.value).toBe('This invite link has expired.')

      postAcceptTerms.mockResolvedValueOnce()
      getState.mockResolvedValue({})
      getMe.mockResolvedValue({ role: 'admin' })
      await s.handleTermsAccept()
      expect(s.termsError.value).toBe('')
    })
  })

  it('handleTermsReject logs out cleanly without ever having registered, clearing inviteExempt with it', async () => {
    postLogout.mockResolvedValue()
    const s = mount()
    s.needsTerms.value = true
    s.inviteExempt.value = true

    await s.handleTermsReject()

    expect(disconnectChat).toHaveBeenCalled()
    expect(s.needsTerms.value).toBe(false)
    expect(s.inviteExempt.value).toBe(false)
    expect(requireLogin).toHaveBeenCalled()
  })

  it('handleLogout does nothing without confirmation, and logs out on confirmation even if the server call fails', async () => {
    confirmDialog.mockResolvedValue(false)
    const s = mount()

    await s.handleLogout()
    expect(postLogout).not.toHaveBeenCalled()
    expect(requireLogin).not.toHaveBeenCalled()

    confirmDialog.mockResolvedValue(true)
    postLogout.mockRejectedValue(new Error('boom'))

    await s.handleLogout()
    expect(disconnectChat).toHaveBeenCalled()
    expect(requireLogin).toHaveBeenCalled()
  })
})
