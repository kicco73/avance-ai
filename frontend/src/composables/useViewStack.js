import { ref } from 'vue'
import { enterScreen } from '../errorStore.js'

export function useViewStack(currentUserRole) {
  const pushedView = ref(null)
  const pushedViewContext = ref({})
  const pushedViewHistory = ref([])
  const chatOpen = ref(false)
  const homePreviewRole = ref(null)
  const showProfile = ref(false)
  const navDirection = ref('forward')
  const slideTransitionName = ref('view-slide-forward')

  function setNavForward() {
    navDirection.value = 'forward'
    slideTransitionName.value = 'view-slide-forward'
  }

  function setNavBack() {
    navDirection.value = 'back'
    slideTransitionName.value = 'view-slide-back'
  }

  function pushView(view, context = {}) {
    setNavForward()
    enterScreen(view, context.projectId ?? '')
    if (view === 'chat') {
      chatOpen.value = true
      return
    }
    if (pushedView.value !== null) {
      pushedViewHistory.value = [...pushedViewHistory.value, { view: pushedView.value, context: pushedViewContext.value }]
    }
    pushedView.value = view
    pushedViewContext.value = context
  }

  function popPushedView() {
    setNavBack()
    if (chatOpen.value) {
      chatOpen.value = false
      enterScreen(pushedView.value ?? 'home')
      return
    }
    const previous = pushedViewHistory.value.at(-1) ?? null
    pushedViewHistory.value = pushedViewHistory.value.slice(0, -1)
    pushedView.value = previous?.view ?? null
    pushedViewContext.value = previous?.context ?? {}
    enterScreen(pushedView.value ?? 'home', pushedViewContext.value.projectId ?? '')
  }

  function openHomePreview(role) {
    setNavForward()
    enterScreen(`home:${role}`)
    homePreviewRole.value = role
  }

  function closeHomePreview() {
    setNavBack()
    enterScreen('home')
    homePreviewRole.value = null
  }

  function goHome() {
    if (currentUserRole.value === 'customer') {
      setNavBack()
      enterScreen('home')
      chatOpen.value = false
      pushedView.value = null
      pushedViewHistory.value = []
      return
    }
    openHomePreview('customer')
  }

  function openProfile() {
    setNavForward()
    enterScreen('profile')
    showProfile.value = true
  }

  function closeProfile() {
    setNavBack()
    showProfile.value = false
  }

  return {
    pushedView, pushedViewContext, chatOpen, homePreviewRole, showProfile, navDirection, slideTransitionName,
    setNavForward, setNavBack, pushView, popPushedView, openHomePreview, closeHomePreview, goHome,
    openProfile, closeProfile,
  }
}
