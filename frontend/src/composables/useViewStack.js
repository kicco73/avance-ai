import { computed, ref } from 'vue'
import { enterScreen } from '../errorStore.js'

export function useViewStack(currentUserRole) {
  const views = ref([])
  const chatOpen = ref(false)
  const homePreviewRole = ref(null)
  const showProfile = ref(false)
  const navDirection = ref('forward')
  const slideTransitionName = ref('view-slide-forward')

  const pushedView = computed(() => views.value.at(-1)?.view ?? null)
  const pushedViewContext = computed(() => views.value.at(-1)?.context ?? {})

  function setNavForward() {
    navDirection.value = 'forward'
    slideTransitionName.value = 'view-slide-forward'
  }

  function setNavBack() {
    navDirection.value = 'back'
    slideTransitionName.value = 'view-slide-back'
  }

  function announceTop() {
    enterScreen(pushedView.value ?? 'home', pushedViewContext.value.projectId ?? '')
  }

  function pushView(view, context = {}) {
    setNavForward()
    if (view === 'chat') {
      enterScreen(view, context.projectId ?? '')
      chatOpen.value = true
      return
    }
    views.value = [...views.value, { view, context }]
    announceTop()
  }

  function popPushedView() {
    setNavBack()
    if (chatOpen.value) chatOpen.value = false
    else views.value = views.value.slice(0, -1)
    announceTop()
  }

  function updateTopContext(patch) {
    const top = views.value.at(-1)
    if (!top) return
    views.value = [...views.value.slice(0, -1), { view: top.view, context: { ...top.context, ...patch } }]
  }

  function resetToRoot() {
    views.value = []
    chatOpen.value = false
    homePreviewRole.value = null
    showProfile.value = false
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
    if (currentUserRole.value === 'supervisor') return openHomePreview('customer')
    setNavBack()
    resetToRoot()
    enterScreen('home')
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
    views, pushedView, pushedViewContext, chatOpen, homePreviewRole, showProfile, navDirection, slideTransitionName,
    setNavForward, setNavBack, pushView, popPushedView, updateTopContext, resetToRoot,
    openHomePreview, closeHomePreview, goHome, openProfile, closeProfile,
  }
}
