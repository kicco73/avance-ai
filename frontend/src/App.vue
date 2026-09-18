<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import LiveChatWindow from './components/chat/LiveChatWindow.vue'
import LoginView from './components/LoginView.vue'
import TermsView from './components/TermsView.vue'
import InviteRequiredView from './components/InviteRequiredView.vue'
import ProfileView from './components/ProfileView.vue'
import ServicesView from './components/services/ServicesView.vue'
import SplashScreen from './components/SplashScreen.vue'
import ErrorBanner from './components/ErrorBanner.vue'
import ToastContainer from './components/ToastContainer.vue'
import HumanTakeoverToasts from './components/HumanTakeoverToasts.vue'
import DialogHost from './components/DialogHost.vue'
import BackgroundAudioPlayer from './components/BackgroundAudioPlayer.vue'
import { requestedOperatorSession, clearRequestedOperatorSession } from './humanTakeoverStore.js'
import { busChannel } from './busChannel.js'
import { needsLogin } from './authStore.js'
import { activeDialog } from './dialogStore.js'
import { useAppBoot } from './composables/useAppBoot.js'
import { useChatFlipTransition } from './composables/useChatFlipTransition.js'
import { useViewStack } from './composables/useViewStack.js'
import { useServerAdminActions } from './composables/useServerAdminActions.js'
import { peekInviteCode } from './shareLink.js'
import { pushedViews, roleHomes } from './skills/registry.js'
import { activeChatSkin, holdSkin } from './chatSkin.js'

const hasSharedInvite = !!peekInviteCode()

const landingProjectId = ref(null)
const currentUserProfile = ref(null)
const currentUserRole = ref(null)
const chatWindowRef = ref(null)
const dialogOpen = computed(() => !!activeDialog.value)
const CORE_OVERLAYS = [
  { view: 'services', component: ServicesView },
]
const overlayView = computed(
  () => [...CORE_OVERLAYS, ...pushedViews.value].find((entry) => entry.view === pushedView.value) ?? null
)

const viewStack = useViewStack(currentUserRole)
const {
  pushedView, pushedViewContext, chatOpen, homePreviewRole, showProfile, navDirection, slideTransitionName,
  setNavBack, pushView, popPushedView, openHomePreview, closeHomePreview, goHome, openProfile, closeProfile,
} = viewStack

const { onChatBeforeEnter, onChatEnter, onChatBeforeLeave, onChatLeave } = useChatFlipTransition(navDirection)

const { handleShowAbout } = useServerAdminActions()

const {
  bootStatus, needsTerms, termsError, inviteExempt,
  startBootSequence,
  handleLoggedIn, handleTermsAccept, handleTermsReject, handleLogout,
} = useAppBoot(
  currentUserProfile, currentUserRole, landingProjectId,
  pushedView, chatOpen, showProfile, navDirection
)

function openChatOn(projectId) {
  landingProjectId.value = projectId
  pushView('chat')
}

function selectLandingProject(projectId) {
  landingProjectId.value = projectId
}

let releaseCoveredHomeSkin = null
watch(
  () => pushedView.value !== null || chatOpen.value || homePreviewRole.value !== null,
  (covered) => {
    releaseCoveredHomeSkin?.()
    releaseCoveredHomeSkin = covered ? holdSkin(activeChatSkin) : null
  }
)

function renameOpenProject(projectId) {
  pushedViewContext.value = { ...pushedViewContext.value, projectId }
}

watch(requestedOperatorSession, (request) => {
  if (!request) return
  chatOpen.value = false
  pushView('operatorChat', { sessionId: request.sessionId })
  clearRequestedOperatorSession()
})

function openChatFromPreview(projectId) {
  return openChatOn(projectId)
}

const profileMenuListeners = { home: goHome, profile: openProfile, logout: handleLogout }

const overlayListeners = {
  ...profileMenuListeners,
  close: popPushedView,
  back: popPushedView,
  renamed: renameOpenProject,
  'project-select': selectLandingProject,
  'home-screen': openHomePreview,
  open: openChatOn,
  'open-chat': openChatOn,
}

const roleHomeListeners = {
  ...profileMenuListeners,
  about: handleShowAbout,
  'open-chat': openChatOn,
  open: openChatOn,
  'open-store': () => pushView('appStore'),
  'project-select': selectLandingProject,
}

const roleHome = computed(() => roleHomes.value.find((entry) => entry.role === currentUserRole.value) ?? null)

const previewedHome = computed(() => roleHomes.value.find((entry) => entry.role === homePreviewRole.value) ?? null)

const homePreviewListeners = {
  ...profileMenuListeners,
  close: closeHomePreview,
  'open-chat': openChatFromPreview,
  open: openChatFromPreview,
  'open-store': () => pushView('appStore'),
  'project-select': selectLandingProject,
}

const liveChatListeners = {
  ...profileMenuListeners,
  'project-select': selectLandingProject,
  'manage-projects': popPushedView,
}

const flipBaseClass = computed(() => ({
  'view-flip-base-flipped': chatOpen.value,
  'view-flip-base-forward': navDirection.value === 'forward',
  'view-flip-base-back': navDirection.value === 'back'
}))

function preventMultiTouchZoom(event) {
  if (event.touches.length > 1) event.preventDefault()
}
function preventGestureZoom(event) {
  event.preventDefault()
}

onMounted(startBootSequence)
onMounted(() => {
  document.addEventListener('touchmove', preventMultiTouchZoom, { passive: false })
  document.addEventListener('gesturestart', preventGestureZoom)
  document.addEventListener('gesturechange', preventGestureZoom)
})
onBeforeUnmount(() => {
  busChannel.disconnect()
  document.removeEventListener('touchmove', preventMultiTouchZoom)
  document.removeEventListener('gesturestart', preventGestureZoom)
  document.removeEventListener('gesturechange', preventGestureZoom)
})
</script>

<template>
  <div class="app-backdrop" aria-hidden="true"></div>

  <ToastContainer />
  <HumanTakeoverToasts />
  <DialogHost />
  <BackgroundAudioPlayer />

  <LoginView v-if="needsLogin" @logged-in="handleLoggedIn" />

  <TermsView v-else-if="needsTerms && (hasSharedInvite || inviteExempt)" :submit-error="termsError" @accept="handleTermsAccept" @reject="handleTermsReject" />
  <InviteRequiredView v-else-if="needsTerms" @logout="handleTermsReject" />

  <SplashScreen v-else-if="bootStatus === 'waiting'" variant="connecting" />
  <SplashScreen v-else-if="bootStatus === 'failed'" variant="failed" @retry="startBootSequence" />

  <div v-else-if="bootStatus === 'ready'" class="app" :class="{ 'app-dialog-open': dialogOpen }">
    <Teleport to="body">
      <ErrorBanner />
    </Teleport>

    <div class="app-body" :class="{ 'app-body-flip-space': currentUserRole === 'admin' || currentUserRole === 'customer' }">
      <LiveChatWindow
        v-if="currentUserRole === 'user' || !roleHome"
        ref="chatWindowRef"
        :project-id="landingProjectId"
        :role="currentUserRole"
        :profile="currentUserProfile"
        v-on="liveChatListeners"
      />

      <template v-else>
        <div class="view-flip-base" :class="flipBaseClass">
          <component
            :is="roleHome.component"
            :key="roleHome.role"
            :project-id="landingProjectId"
            :current-user-role="currentUserRole"
            :profile="currentUserProfile"
            :view-stack="viewStack"
            v-on="roleHomeListeners"
          />

          <Transition :name="slideTransitionName">
            <LiveChatWindow
              v-if="homePreviewRole === 'user'"
              role="admin"
              :project-id="landingProjectId"
              :profile="currentUserProfile"
              @project-select="selectLandingProject"
              @manage-projects="closeHomePreview"
              v-on="profileMenuListeners"
            />
            <component
              v-else-if="previewedHome"
              :is="previewedHome.component"
              :key="previewedHome.role"
              :standalone="false"
              :project-id="landingProjectId"
              :current-user-role="currentUserRole"
              :profile="currentUserProfile"
              :view-stack="viewStack"
              v-on="homePreviewListeners"
            />
          </Transition>

          <Transition :name="slideTransitionName">
            <component
              v-if="overlayView"
              :is="overlayView.component"
              :key="`${pushedView}-${pushedViewContext.projectId}-${pushedViewContext.sessionId}`"
              :project-id="pushedViewContext.projectId"
              :session-id="pushedViewContext.sessionId"
              :build-error="pushedViewContext.buildError"
              :current-user-role="currentUserRole"
              :profile="currentUserProfile"
              :view-stack="viewStack"
              v-on="overlayListeners"
            />
          </Transition>
        </div>

        <Transition
          :css="false"
          @before-enter="onChatBeforeEnter"
          @enter="onChatEnter"
          @before-leave="onChatBeforeLeave"
          @leave="onChatLeave"
        >
          <LiveChatWindow
            v-if="chatOpen"
            ref="chatWindowRef"
            :project-id="landingProjectId"
            :role="currentUserRole"
            :profile="currentUserProfile"
            v-on="liveChatListeners"
          />
        </Transition>
      </template>
    </div>

    <Transition :name="slideTransitionName">
      <ProfileView v-if="showProfile" @close="closeProfile" />
    </Transition>
  </div>
</template>

<style scoped>
.app-backdrop {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  z-index: -1;
  pointer-events: none;
  background: var(--app-base-gradient);
}

.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  font-family: system-ui, -apple-system, sans-serif;
  transform: none;
  filter: none;
  transition: transform 0.2s ease-in-out, filter 0.2s ease-in-out;
}

.app-dialog-open {
  transform: scale(0.9);
  filter: blur(3px);
  padding-bottom: var(--viewport-bottom-overshoot, 0px);
}

.app-dialog-open .app-body {
  --viewport-bottom-overshoot: 0px;
}

.app-body {
  position: relative;
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
  --flip-duration: 500ms;
}

.app-body-flip-space {
  perspective: 1300px;
}

.view-flip-base {
  flex: 1;
  min-height: 0;
  backface-visibility: hidden;
  -webkit-backface-visibility: hidden;
  will-change: transform;
}

.view-flip-base-forward {
  transition: transform var(--flip-duration) ease-in;
}

.view-flip-base-back {
  transition: transform var(--flip-duration) ease-out;
  transition-delay: var(--flip-duration);
}

.view-flip-base-flipped {
  transform: rotateY(90deg);
}

.view-slide-forward-enter-active,
.view-slide-forward-leave-active,
.view-slide-back-enter-active,
.view-slide-back-leave-active {
  transition: transform 0.32s cubic-bezier(0.22, 1, 0.36, 1);
}

.view-slide-forward-enter-active,
.view-slide-back-leave-active {
  z-index: 101 !important;
}

.view-slide-forward-enter-from {
  transform: translateX(100%);
}

.view-slide-back-leave-to {
  transform: translateX(100%);
}

.home-preview-admin-wrap {
  position: fixed;
  inset: 0;
}

.home-preview-admin-back-btn {
  position: absolute;
  top: calc(0.75rem + var(--safe-area-top));
  left: calc(0.75rem + var(--safe-area-left));
  z-index: 200;
  background: white;
}

.upload-model-input {
  display: none;
}
</style>
