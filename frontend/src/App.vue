<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import LiveChatWindow from './components/chat/LiveChatWindow.vue'
import HumanOperatorChatView from './components/chat/HumanOperatorChatView.vue'
import EditProjectView from './components/project/edit/EditProjectView.vue'
import LabelProjectView from './components/project/label/LabelProjectView.vue'
import LoginView from './components/LoginView.vue'
import TermsView from './components/TermsView.vue'
import InviteRequiredView from './components/InviteRequiredView.vue'
import ProfileView from './components/ProfileView.vue'
import ManageProjectsView from './components/settings/ManageProjectsView.vue'
import ManageUsersView from './components/settings/ManageUsersView.vue'
import ServicesView from './components/settings/ServicesView.vue'
import AppStoreView from './components/appStore/AppStoreView.vue'
import CustomerHome from './components/appStore/CustomerHome.vue'
import SplashScreen from './components/SplashScreen.vue'
import ErrorBanner from './components/ErrorBanner.vue'
import ToastContainer from './components/ToastContainer.vue'
import HumanTakeoverToasts from './components/HumanTakeoverToasts.vue'
import DialogHost from './components/DialogHost.vue'
import { requestedOperatorSession, clearRequestedOperatorSession } from './humanTakeoverStore.js'
import './humanPromptBus.js'
import { disconnect as disconnectChat } from './chatClient.js'
import { needsLogin } from './authStore.js'
import { activeDialog } from './dialogStore.js'
import { useAppBoot } from './composables/useAppBoot.js'
import { useChatFlipTransition } from './composables/useChatFlipTransition.js'
import { useViewStack } from './composables/useViewStack.js'
import { useProjectAdminActions } from './composables/useProjectAdminActions.js'
import { peekInviteCode } from './shareLink.js'

const hasSharedInvite = !!peekInviteCode()

const editProjectId = ref(null)
const editProjectBuildError = ref(null)
const labelProjectId = ref(null)
const liveChatProjectId = ref(null)
const operatorSessionId = ref(null)
const currentUserProfile = ref(null)
const currentUserRole = ref(null)
const chatWindowRef = ref(null)
const manageProjectsView = ref(null)
const customerHomeView = ref(null)
const dialogOpen = computed(() => !!activeDialog.value)

const {
  pushedView, chatOpen, homePreviewRole, showProfile, navDirection, slideTransitionName,
  setNavBack, pushView, popPushedView, openHomePreview, closeHomePreview, goHome, openProfile, closeProfile,
} = useViewStack(currentUserRole, customerHomeView)

const { onChatBeforeEnter, onChatEnter, onChatBeforeLeave, onChatLeave } = useChatFlipTransition(navDirection)

const {
  modelUploadInput, uploadingProject, uploadProgress, uploadProjectId, uploadIconReady,
  triggerModelUpload, handleNewProject, handleModelUploadChange, handleModelEditSaved, handleProjectSwitch,
  activateAndRefresh, handleModelDownload, handleModelDelete, handleWipeAllLiveSessions,
  handleCleanUnusedRevisions, handleDownloadBackup, handleRestoreBackup, handleShowAbout,
} = useProjectAdminActions(chatWindowRef, manageProjectsView)

const {
  bootStatus, needsTerms, termsError, inviteExempt,
  startBootSequence,
  handleLoggedIn, handleTermsAccept, handleTermsReject, handleLogout,
} = useAppBoot(
  currentUserProfile, currentUserRole, labelProjectId, liveChatProjectId,
  pushedView, chatOpen, showProfile, navDirection
)

// The embedded "Test" chat runs against the server-side active project, so
// opening Edit for a non-active project activates it first.
async function handleModelEdit(projectId, buildError = null) {
  await activateAndRefresh(projectId)
  editProjectId.value = projectId
  editProjectBuildError.value = buildError
  pushView('edit')
}

function handleSelectLabelSessions(projectId) {
  labelProjectId.value = projectId
  pushView('label')
}

function handleManageProjectsChat(projectId) {
  pushView('chat')
  handleLiveChatProjectSelect(projectId)
}

function handleLiveChatProjectSelect(projectId) {
  liveChatProjectId.value = projectId
  handleProjectSwitch(projectId)
}

function handleSettingsManageUsers() {
  pushView('manageUsers')
}

function handleSettingsManageServices() {
  pushView('services')
}

function handleSettingsAppStore() {
  pushView('appStore')
}

async function handleLabelProjectSwitch(projectId) {
  await handleProjectSwitch(projectId)
  labelProjectId.value = projectId
}

// A human_takeover toast's own "Open" link (see humanTakeoverStore.js) —
// requestedOperatorSession is the one thing that store hands upward,
// since only App.vue holds pushView/the view stack.
watch(requestedOperatorSession, (request) => {
  if (!request) return
  // chatOpen and pushedView are independent refs (see useViewStack.js) —
  // every existing pushView(x !== 'chat') caller only ever runs from a
  // screen where chatOpen is already false, so this never mattered before.
  // A takeover toast can fire from anywhere, including from inside the
  // admin's own already-open live chat — without this, both would render
  // at once.
  chatOpen.value = false
  operatorSessionId.value = request.sessionId
  pushView('operatorChat')
  clearRequestedOperatorSession()
})

function openChatFromPreview(projectId) {
  closeHomePreview()
  handleManageProjectsChat(projectId)
}

function openStoreFromPreview() {
  closeHomePreview()
  handleSettingsAppStore()
}

const profileMenuListeners = { home: goHome, profile: openProfile, logout: handleLogout }

const liveChatListeners = {
  ...profileMenuListeners,
  'project-select': handleLiveChatProjectSelect,
  'project-download': handleModelDownload,
  'manage-projects': popPushedView,
}

const manageProjectsListeners = {
  ...profileMenuListeners,
  'new-project': handleNewProject,
  upload: triggerModelUpload,
  delete: handleModelDelete,
  edit: handleModelEdit,
  label: handleSelectLabelSessions,
  download: handleModelDownload,
  'manage-users': handleSettingsManageUsers,
  'manage-services': handleSettingsManageServices,
  'app-store': handleSettingsAppStore,
  about: handleShowAbout,
}

const flipBaseClass = computed(() => ({
  'view-flip-base-flipped': chatOpen.value,
  'view-flip-base-forward': navDirection.value === 'forward',
  'view-flip-base-back': navDirection.value === 'back'
}))

// touch-action alone was observed not to stop pinch-zoom on plain backdrops;
// a 2+-finger touchmove is the pinch gesture itself.
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
  disconnectChat()
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
        v-if="currentUserRole === 'user'"
        ref="chatWindowRef"
        :project-id="liveChatProjectId"
        :role="currentUserRole"
        :profile="currentUserProfile"
        v-on="liveChatListeners"
      />

      <template v-else-if="currentUserRole === 'customer'">
        <div class="view-flip-base" :class="flipBaseClass">
          <CustomerHome
            ref="customerHomeView"
            :profile="currentUserProfile"
            @open="handleManageProjectsChat"
            @open-store="handleSettingsAppStore"
            v-on="profileMenuListeners"
          />

          <Transition :name="slideTransitionName">
            <AppStoreView
              v-if="pushedView === 'appStore'"
              :profile="currentUserProfile"
              @close="popPushedView"
              @open="handleManageProjectsChat"
              v-on="profileMenuListeners"
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
            :project-id="liveChatProjectId"
            :role="currentUserRole"
            :profile="currentUserProfile"
            v-on="liveChatListeners"
          />
        </Transition>
      </template>

      <LabelProjectView
        v-else-if="currentUserRole === 'supervisor'"
        :key="labelProjectId"
        :project-id="labelProjectId"
        :profile="currentUserProfile"
        @project-select="handleLabelProjectSwitch"
        v-on="profileMenuListeners"
      />

      <template v-else-if="currentUserRole === 'admin'">
        <div class="view-flip-base" :class="flipBaseClass">
          <ManageProjectsView
            ref="manageProjectsView"
            :uploading="uploadingProject"
            :upload-progress="uploadProgress"
            :upload-project-id="uploadProjectId"
            :upload-icon-ready="uploadIconReady"
            role="admin"
            :profile="currentUserProfile"
            @chat="handleManageProjectsChat"
            v-on="manageProjectsListeners"
          />

          <Transition :name="slideTransitionName">
            <EditProjectView
              v-if="pushedView === 'edit'"
              :key="editProjectId"
              :project-id="editProjectId"
              :build-error="editProjectBuildError"
              :profile="currentUserProfile"
              @saved="handleModelEditSaved"
              @back="popPushedView"
              v-on="profileMenuListeners"
            />
            <LabelProjectView
              v-else-if="pushedView === 'label'"
              :key="labelProjectId"
              :project-id="labelProjectId"
              :profile="currentUserProfile"
              @close="popPushedView"
              @project-select="handleLabelProjectSwitch"
              v-on="profileMenuListeners"
            />
            <ManageUsersView
              v-else-if="pushedView === 'manageUsers'"
              :current-user-role="currentUserRole"
              :profile="currentUserProfile"
              @close="popPushedView"
              @home-screen="openHomePreview"
              v-on="profileMenuListeners"
            />
            <ServicesView
              v-else-if="pushedView === 'services'"
              :profile="currentUserProfile"
              @close="popPushedView"
              @download-backup="handleDownloadBackup"
              @restore-backup="handleRestoreBackup"
              @wipe-live-sessions="handleWipeAllLiveSessions"
              @clean-unused-revisions="handleCleanUnusedRevisions"
              v-on="profileMenuListeners"
            />
            <AppStoreView
              v-else-if="pushedView === 'appStore'"
              :profile="currentUserProfile"
              @close="popPushedView"
              @open="handleManageProjectsChat"
              v-on="profileMenuListeners"
            />
            <HumanOperatorChatView
              v-else-if="pushedView === 'operatorChat'"
              :key="operatorSessionId"
              :session-id="operatorSessionId"
              @close="popPushedView"
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
            :project-id="liveChatProjectId"
            :role="currentUserRole"
            :profile="currentUserProfile"
            v-on="liveChatListeners"
          />
        </Transition>
      </template>

      <input
        ref="modelUploadInput"
        type="file"
        accept=".zip,.yml,.yaml"
        class="upload-model-input"
        @change="handleModelUploadChange"
      />
    </div>

    <Transition :name="slideTransitionName">
      <LiveChatWindow
        v-if="homePreviewRole === 'user'"
        role="admin"
        :project-id="liveChatProjectId"
        :profile="currentUserProfile"
        @project-select="handleLiveChatProjectSelect"
        @project-download="handleModelDownload"
        @manage-projects="closeHomePreview"
        v-on="profileMenuListeners"
      />
      <CustomerHome
        v-else-if="homePreviewRole === 'customer'"
        :standalone="false"
        :profile="currentUserProfile"
        @close="closeHomePreview"
        @open="openChatFromPreview"
        @open-store="openStoreFromPreview"
        v-on="profileMenuListeners"
      />
      <LabelProjectView
        v-else-if="homePreviewRole === 'supervisor'"
        :key="labelProjectId"
        :project-id="labelProjectId"
        :profile="currentUserProfile"
        @close="closeHomePreview"
        @project-select="handleLabelProjectSwitch"
        v-on="profileMenuListeners"
      />
      <div v-else-if="homePreviewRole === 'admin'" class="home-preview-admin-wrap">
        <button type="button" class="app-header-icon-btn home-preview-admin-back-btn" title="Back" @click="closeHomePreview">«</button>
        <ManageProjectsView
          :uploading="uploadingProject"
          :upload-progress="uploadProgress"
          :upload-project-id="uploadProjectId"
          :upload-icon-ready="uploadIconReady"
          role="admin"
          :profile="currentUserProfile"
          @chat="openChatFromPreview"
          v-on="manageProjectsListeners"
        />
      </div>
    </Transition>

    <Transition :name="slideTransitionName">
      <ProfileView v-if="showProfile" @close="closeProfile" />
    </Transition>
  </div>
</template>

<style scoped>
/* Full-viewport surfaces extend their bottom by --viewport-bottom-overshoot
   (see useVisualViewport.js) and must never get transform/filter on their
   root, or WebKit clips them to the short standalone-iOS viewport. */
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
  /* 100vh, not 100dvh: iOS standalone webapps leave a gap with dvh. */
  height: 100vh;
  font-family: system-ui, -apple-system, sans-serif;
  /* none (not scale(1)/blur(0)) so position:fixed descendants keep the real viewport. */
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
