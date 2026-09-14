import { ref } from 'vue'
import { getState, getMe, getProjects, postRedeemInviteCode, activateProject, postAcceptTerms, postLogout, getPendingStatus } from '../api.js'
import { busChannel } from '../busChannel.js'
import { clearApiError } from '../errorStore.js'
import { requireLogin } from '../authStore.js'
import { confirmDialog } from '../dialogStore.js'
import { consumeInviteCode, peekInviteCode } from '../shareLink.js'
import { loadSkillRoster } from '../skillRoster.js'
import { chatChannel, liveChatObservers, messageListeners, modelSelectors, stateListeners } from '../skills/registry.js'
import { observeMessages } from '../messageNotifier.js'
import { installModelSelector, modelSelector } from '../modelSelector.js'
import { installChatChannel } from '../liveChatChannel.js'
import { setInputTokenBudgetPerTurn, setTotalTokenBudgetPerSession, handleStateChange, loadMessages, observeLiveChat } from '../chatStore.js'
import { watchPushedTasks } from '../notificationBus.js'

export function useAppBoot(
  currentUserProfile, currentUserRole, landingProjectId,
  pushedView, chatOpen, showProfile, navDirection
) {
  const bootStatus = ref('checking')
  const needsTerms = ref(false)
  const inviteExempt = ref(false)
  const termsError = ref('')

  const PING_INTERVAL_MS = 800
  const PING_TIMEOUT_MS = 3000
  const MAX_PING_ATTEMPTS = 30

  let pingAttempts = 0
  let pingTimeoutHandle = null
  let bootSequenceToken = 0

  async function pingBackend() {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), PING_TIMEOUT_MS)
    try {
      const newState = await getState(controller.signal)
      setInputTokenBudgetPerTurn(newState.input_token_budget_per_turn ?? null)
      setTotalTokenBudgetPerSession(newState.total_token_budget_per_session ?? null)
      handleStateChange(newState)
      await loadSkillRoster()
      observeMessages(messageListeners.value)
      installModelSelector(modelSelectors.value)
      installChatChannel(chatChannel.value)
      watchPushedTasks()
      observeLiveChat(liveChatObservers.value)
      publishState(newState)
      return 'ready'
    } catch (err) {
      if (err.status === 403) return 'pending'
      if (err.status === 401) return 'unauthorized'
      return 'retry'
    } finally {
      clearTimeout(timeout)
    }
  }

  function publishState(newState) {
    for (const listener of stateListeners.value) {
      try {
        listener.stateReceived(newState)
      } catch (err) {
        console.error('a skill failed to read the boot state', err)
      }
    }
  }

  function bootSucceeded() {
    bootStatus.value = 'ready'
    clearApiError()
    if (currentUserRole.value === 'user' || chatOpen.value) {
      loadMessages(landingProjectId.value)
    }
    modelSelector().load()
    busChannel.connect()
  }

  async function getActiveProjectId() {
    try {
      const res = await getProjects()
      return res.active ?? res.projects[0]?.name ?? null
    } catch {
      return null
    }
  }

  async function activateInvitedProject() {
    const code = consumeInviteCode()
    if (!code) return null
    try {
      const { project_id: projectId } = await postRedeemInviteCode(code)
      if (!projectId) return null
      await activateProject(projectId)
      return projectId
    } catch {
      return null
    }
  }

  async function resolveLandingView() {
    pushedView.value = null
    chatOpen.value = false
    showProfile.value = false
    navDirection.value = 'forward'
    try {
      currentUserProfile.value = await getMe()
      currentUserRole.value = currentUserProfile.value?.role ?? null
    } catch {
      return
    }
    const sharedProjectId = await activateInvitedProject()
    landingProjectId.value = sharedProjectId ?? await getActiveProjectId()
    if (sharedProjectId && currentUserRole.value !== 'user' && currentUserRole.value !== 'supervisor') {
      chatOpen.value = true
    }
  }

  async function runPingAttempt(token) {
    if (token !== bootSequenceToken) return
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
      try {
        inviteExempt.value = (await getPendingStatus()).invite_exempt
      } catch {
        inviteExempt.value = false
      }
      if (token !== bootSequenceToken) return
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

  function handleLoggedIn() {
    startBootSequence()
  }

  async function handleTermsAccept() {
    termsError.value = ''
    try {
      await postAcceptTerms(peekInviteCode())
    } catch (err) {
      termsError.value = err.detail || err.message || 'Could not complete registration.'
      return
    }
    needsTerms.value = false
    startBootSequence()
  }

  async function handleTermsReject() {
    try {
      await postLogout()
    } catch {
    }
    busChannel.disconnect()
    needsTerms.value = false
    termsError.value = ''
    inviteExempt.value = false
    requireLogin()
  }

  async function handleLogout() {
    const ok = await confirmDialog({ title: 'Log out', body: 'Log out of Avance?', okLabel: 'Log out' })
    if (!ok) return
    try {
      await postLogout()
    } catch {
    }
    busChannel.disconnect()
    requireLogin()
  }

  return {
    bootStatus, needsTerms, termsError, inviteExempt,
    getActiveProjectId, resolveLandingView, startBootSequence,
    handleLoggedIn, handleTermsAccept, handleTermsReject, handleLogout,
  }
}
