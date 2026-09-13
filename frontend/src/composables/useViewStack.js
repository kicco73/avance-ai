import { ref } from 'vue'

export function useViewStack(currentUserRole) {
  const pushedView = ref(null)
  const pushedViewContext = ref({})
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
    pushedViewContext.value = context
    if (view === 'chat') chatOpen.value = true
    else pushedView.value = view
  }

  function popPushedView() {
    setNavBack()
    if (chatOpen.value) {
      chatOpen.value = false
      return
    }
    pushedView.value = null
  }

  function openHomePreview(role) {
    setNavForward()
    homePreviewRole.value = role
  }

  function closeHomePreview() {
    setNavBack()
    homePreviewRole.value = null
  }

  function goHome() {
    if (currentUserRole.value === 'customer') {
      setNavBack()
      chatOpen.value = false
      pushedView.value = null
      return
    }
    openHomePreview('customer')
  }

  function openProfile() {
    setNavForward()
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
