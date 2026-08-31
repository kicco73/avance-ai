<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import LiveChatWindow from './components/chat/LiveChatWindow.vue'
import EditProjectView from './components/project/edit/EditProjectView.vue'
import LabelProjectView from './components/project/label/LabelProjectView.vue'
import LoginView from './components/LoginView.vue'
import TermsView from './components/TermsView.vue'
import InviteRequiredView from './components/InviteRequiredView.vue'
import ProfileView from './components/ProfileView.vue'
import ManageProjectsView from './components/settings/ManageProjectsView.vue'
import ManageUsersView from './components/settings/ManageUsersView.vue'
import SplashScreen from './components/SplashScreen.vue'
import ErrorBanner from './components/ErrorBanner.vue'
import ToastContainer from './components/ToastContainer.vue'
import DialogHost from './components/DialogHost.vue'
import {
  getState,
  putProject,
  postNewProject,
  activateProject,
  deleteProject,
  postWipeLiveSessions,
  downloadProject,
  getBackup,
  postRestoreBackup,
  postPublishProject,
  getAbout
} from './api.js'
import { disconnect as disconnectChat } from './chatClient.js'
import { needsLogin } from './authStore.js'
import { activeDialog, aboutDialog, confirmDialog } from './dialogStore.js'
import {
  handleStateChange,
  loadMessages,
  clearChatUi
} from './chatStore.js'
import { useAppBoot } from './composables/useAppBoot.js'
import { useChatFlipTransition } from './composables/useChatFlipTransition.js'
import { peekInviteCode } from './shareLink.js'

// Read once, at this component's own setup — the only mount App.vue
// ever gets, and shareLink.js's own module-level capture has already
// run by then (import graphs resolve before any component's setup()
// body executes). Whether a pending (unregistered) identity gets
// TermsView (this app's own self-service registration) or
// InviteRequiredView (registration refused — see AuthService.
// complete_registration) hinges on this or useAppBoot.js's own
// inviteExempt (the two pre-wired admin addresses, see
// AuthService.is_invite_exempt): registration is invite-only now,
// recognizable only by having arrived via a "share project" link or
// being one of those two addresses. A truthy code here is no
// guarantee it's still valid (expiry/max-shares are only checked
// server-side, at Accept) — see termsError/TermsView's own
// submitError for that outcome.
const hasSharedInvite = !!peekInviteCode()

const editProjectName = ref(null)
const labelProjectName = ref(null)
const liveChatProjectName = ref(null)
const showProfile = ref(false)
// Admin only: what's currently pushed over the permanently-mounted
// ManageProjectsView base — null | 'edit' | 'label' | 'manageUsers' |
// 'chat'. A plain 'user' has no stack at all (chat is the whole app); a
// 'supervisor' has no stack either (LabelProjectView is the whole app).
const pushedView = ref(null)
// Which way the next transition (see the <Transition>s below) should go —
// 'forward' for anything that pushes a new view over another, 'back' only
// for a pop. Sets, not toggles: every navigation call site names its own
// direction explicitly via setNavForward/setNavBack rather than inferring
// it from anything.
const navDirection = ref('forward')
// The actual :name passed to the 2D slide <Transition>s, set explicitly
// and synchronously alongside navDirection/pushedView by
// pushView/popPushedView below — not derived at render time via a ternary
// on navDirection. A ternary there raced against pushedView's own v-if
// change (both flip in the same tick) and could resolve against a stale
// combination. The chat flip below sidesteps this differently — see its
// own JS transition hooks further down.
const slideTransitionName = ref('view-slide-forward')
function setNavForward() {
  navDirection.value = 'forward'
  slideTransitionName.value = 'view-slide-forward'
}
function setNavBack() {
  navDirection.value = 'back'
  slideTransitionName.value = 'view-slide-back'
}
function pushView(view) {
  setNavForward()
  pushedView.value = view
}
function popPushedView() {
  setNavBack()
  pushedView.value = null
}

const { onChatBeforeEnter, onChatEnter, onChatBeforeLeave, onChatLeave } = useChatFlipTransition(navDirection)
const dialogOpen = computed(() => !!activeDialog.value)
const modelUploadInput = ref(null)
const chatWindowRef = ref(null)
const manageProjectsView = ref(null)
const uploadingProject = ref(false)
// 0-100, or null before the first progress chunk has arrived — see
// SessionsTree.vue's identical importProgress for the same reasoning.
const uploadProgress = ref(null)
const uploadProjectName = ref(null)
const uploadIconReady = ref(false)
// Fetched once, up front (see resolveLandingView) — the role-based
// landing routing needs it before the very first render, and ProfileMenu.vue's
// own avatar reuses the same fetch instead of a second, redundant
// /api/auth/me call (see its own `profile` prop below).
const currentUserProfile = ref(null)
const currentUserRole = ref(null)

const {
  bootStatus, needsTerms, termsError, inviteExempt,
  getActiveProjectName, startBootSequence,
  handleLoggedIn, handleTermsAccept, handleTermsReject, handleLogout,
} = useAppBoot(
  currentUserProfile, currentUserRole, labelProjectName, liveChatProjectName,
  pushedView, showProfile, navDirection
)

function triggerModelUpload() {
  if (uploadingProject.value) return
  modelUploadInput.value?.click()
}

// The fetch-fresh-state-and-redisplay half of the reset/switch/upload/
// delete flow (see chatStore.js's clearChatUi for the optimistic-clear
// half each of those runs first): the fresh state comes from a separate
// GET /api/state call (none of putModel/activateModel/deleteModel's
// responses carry the state payload itself), same as handleReset picks up
// the opening message via REST, regardless of chat transport.
async function refreshStateAndProjects() {
  const newState = await getState()
  chatWindowRef.value?.refreshProjectsMenu()
  manageProjectsView.value?.refresh()
  handleStateChange(newState)
}

// "New project": same server-side effect as picking samples/Hello
// world.zip in the upload dialog (see postNewProject), so it reloads
// state the same way a real upload does — including auto-publishing (see
// handleModelUploadChange's own identical reasoning below): a freshly
// created project has never been published either, so without this it'd
// look usable right away but couldn't actually chat yet.
async function handleNewProject() {
  clearChatUi()
  try {
    const result = await postNewProject()
    await postPublishProject(result.project_name)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

async function handleModelUploadChange(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // allow re-selecting the same file afterward
  if (!file) return

  const projectName = file.name.replace(/\.(zip|ya?ml)$/i, '')
  clearChatUi()
  uploadingProject.value = true
  uploadProgress.value = null
  uploadProjectName.value = projectName
  uploadIconReady.value = false
  try {
    await putProject(projectName, file, (message) => {
      uploadProgress.value = message.percentage
    }, () => {
      uploadIconReady.value = true
    })
    // A freshly uploaded project has never been published — nothing can
    // chat with it yet (see db.create_chat_session, which requires a
    // published_revision) until someone opens "Edit project" and clicks
    // Publish. Doing that automatically here means an upload is usable
    // right away, same as it always visibly appeared to be.
    await postPublishProject(projectName)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  } finally {
    uploadingProject.value = false
    uploadProgress.value = null
    uploadProjectName.value = null
    uploadIconReady.value = false
  }
}

// EditProjectView.vue's own embedded "Test" chat creates its draft
// session against whichever project is currently *active* server-side
// (see ChatService.create_draft_session — it never actually looks at the
// URL's own project_name), an invariant every previous way into "Edit
// project" upheld for free: before Manage projects made a project's own
// row directly clickable, the only path in was ProjectsMenu.vue's own
// "Edit project" item, which only ever edited whichever project was
// already active. That's no longer guaranteed — Manage projects lets you
// open Edit for a project that isn't active at all — so this activates
// `projectName` first, same as a real project switch, before ever
// opening the view: without this, Test silently runs against whatever
// project was active before, not the one actually being edited.
async function handleModelEdit(projectName) {
  clearChatUi()
  try {
    await activateProject(projectName)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
  editProjectName.value = projectName
  pushView('edit')
}

function handleSelectLabelSessions(projectName) {
  labelProjectName.value = projectName
  pushView('label')
}

// "Open chat" on a project's own row: same switch as picking it from
// ProjectsMenu — ManageProjectsView is never unmounted, so this just
// pushes chat over it.
function handleManageProjectsChat(projectName) {
  pushView('chat')
  handleLiveChatProjectSelect(projectName)
}

function handleLiveChatProjectSelect(projectName) {
  liveChatProjectName.value = projectName
  handleProjectSwitch(projectName)
}

// Fed only by ChatView.vue's own admin-only back arrow now (the Settings
// menu no longer has a "Manage projects" item) — pops back to the
// permanent ManageProjectsView base, which is never unmounted.
function handleSettingsManageProjects() {
  popPushedView()
}

function handleSettingsManageUsers() {
  pushView('manageUsers')
}

async function handleSettingsLabelSessions() {
  const projectName = await getActiveProjectName()
  if (!projectName) return
  labelProjectName.value = projectName
  if (currentUserRole.value === 'admin') pushView('label')
}

async function handleSettingsEditProjects() {
  const projectName = await getActiveProjectName()
  if (!projectName) return
  await handleModelEdit(projectName)
}

async function handleModelEditSaved() {
  clearChatUi()
  try {
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

// Activation is idempotent backend-side (re-activating the already-active
// model is a no-op, no reset) so this handler doesn't need to
// special-case that itself.
async function handleProjectSwitch(projectName) {
  clearChatUi()
  try {
    await activateProject(projectName)
    await refreshStateAndProjects()
    await loadMessages()
  } catch {
    // already surfaced via apiFetch
  }
}

// LabelProjectView's own ProjectsMenu, for switching project without
// leaving the label view. Reuses the same activation as a normal switch,
// then repoints the view at the new project — its :key below remounts it,
// same as opening it fresh from Manage projects.
async function handleLabelProjectSwitch(projectName) {
  await handleProjectSwitch(projectName)
  labelProjectName.value = projectName
}

// Triggers a browser download from the zip blob — standard synthetic-<a>
// pattern, since fetch() has no way to hand a response straight to the
// browser's own download UI. No UI state changes at all on success: unlike
// switch/upload/delete, downloading doesn't touch the active model or the
// session. On failure, show the error the same way as the rest of the menu.
async function handleModelDownload(projectName) {
  try {
    const blob = await downloadProject(projectName)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${projectName}.zip`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch {
    // already surfaced via apiFetch
  }
}

// Deleting the active model falls back to whatever project's left
// backend-side (or none at all — see the "no-project" splash below), so
// this behaves the same as a successful switch/upload either way — reload
// state, clear the chat.
async function handleModelDelete(projectName) {
  clearChatUi()
  try {
    await deleteProject(projectName)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

async function handleManageProjectsWipeLiveSessions(projectName) {
  try {
    await postWipeLiveSessions(projectName)
  } catch {
    // already surfaced via apiFetch
  }
}

// Whole-database download (every project, session, message, signal — not
// scoped to the active project), unlike handleModelDownload's per-project
// zip. No UI state changes on success, same reasoning as that one.
async function handleDownloadBackup() {
  try {
    const blob = await getBackup()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'avance-backup.sqlite'
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch {
    // already surfaced via apiFetch
  }
}

// Replaces the entire working database server-side — every project,
// session, and message the server currently has is gone either way, so
// this needs the same explicit confirmation as handleReset (chatStore.js),
// then the same reload-everything path as switch/upload/delete.
async function handleRestoreBackup(file) {
  const ok = await confirmDialog({
    title: 'Restore backup',
    body: 'Restore this backup? This replaces the entire working database (all projects, sessions, and messages) and cannot be undone.',
    okLabel: 'Restore',
    danger: true
  })
  if (!ok) return
  clearChatUi()
  try {
    await postRestoreBackup(file)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

// SettingsMenu's "About Avance..." — name/version straight off the
// running backend (main.py's own __version__), fetched fresh on every
// open rather than cached, so it always reflects whatever build is
// actually serving the request.
async function handleShowAbout() {
  try {
    const about = await getAbout()
    await aboutDialog({ version: about.version })
  } catch {
    // already surfaced via apiFetch
  }
}

function openProfile() {
  setNavForward()
  showProfile.value = true
}

// Belt-and-suspenders for the html/body touch-action: pan-x pan-y rule
// above (see that rule's own comment) — CSS touch-action is supposed to
// suppress pinch-zoom outright, but was still observed reachable on some
// plain, unstyled backdrop (no bubble, no button, nothing with its own
// touch-action) on a real device, which the CSS alone should already
// have covered. Two independent mechanisms instead of trusting the one
// that apparently isn't fully honored everywhere: a 2+-finger touchmove
// is exactly the gesture that drives pinch-zoom, and preventDefault on
// it blocks the zoom regardless of which element started the touch;
// gesturestart/gesturechange are WebKit's own separate pinch-gesture
// events (harmless no-op on engines that never fire them). { passive:
// false } is required for preventDefault to actually take effect on
// touchmove — the default listener is passive, exactly to let scrolling
// stay fast, but that same default is what silently no-ops
// preventDefault here if left unset.
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
  if (pingTimeoutHandle) clearTimeout(pingTimeoutHandle)
  document.removeEventListener('touchmove', preventMultiTouchZoom)
  document.removeEventListener('gesturestart', preventGestureZoom)
  document.removeEventListener('gesturechange', preventGestureZoom)
})
</script>

<template>
  <!-- Always mounted, outside every branch below — the real element that
       actually covers the standalone-iOS gap under a shrunk/faded
       full-viewport screen (see its own style for why the html/body
       background alone couldn't). -->
  <div class="app-backdrop" aria-hidden="true"></div>

  <!-- 'checking' (the invisible first ping) renders neither branch, on
       purpose: nothing should flash before we know whether the backend was
       already up. -->
  <ToastContainer />
  <DialogHost />

  <!-- Overrides everything below regardless of bootStatus — a 401 (see
       api.js's apiFetch) can happen at any point, including mid-boot. -->
  <LoginView v-if="needsLogin" @logged-in="handleLoggedIn" />

  <TermsView v-else-if="needsTerms && (hasSharedInvite || inviteExempt)" :submit-error="termsError" @accept="handleTermsAccept" @reject="handleTermsReject" />
  <InviteRequiredView v-else-if="needsTerms" @logout="handleTermsReject" />

  <SplashScreen v-else-if="bootStatus === 'waiting'" variant="connecting" />
  <SplashScreen v-else-if="bootStatus === 'failed'" variant="failed" @retry="startBootSequence" />

  <div v-else-if="bootStatus === 'ready'" class="app" :class="{ 'app-dialog-open': dialogOpen }">
    <ErrorBanner />

    <div class="app-body" :class="{ 'app-body-flip-space': currentUserRole === 'admin' }">
      <!-- Plain user: chat is the entire app, no stack, no transition. -->
      <LiveChatWindow
        v-if="currentUserRole === 'user'"
        ref="chatWindowRef"
        :project-name="liveChatProjectName"
        :role="currentUserRole"
        :profile="currentUserProfile"
        @project-select="handleLiveChatProjectSelect"
        @project-download="handleModelDownload"
        @manage-projects="handleSettingsManageProjects"
        @profile="openProfile"
        @logout="handleLogout"
      />

      <!-- Supervisor: Label sessions is the entire app, no stack, no
           transition — Settings can only re-point it at another active
           project (handleSettingsLabelSessions), never push/pop it. -->
      <LabelProjectView
        v-else-if="currentUserRole === 'supervisor'"
        :key="labelProjectName"
        :project-name="labelProjectName"
        role="supervisor"
        :profile="currentUserProfile"
        @project-select="handleLabelProjectSwitch"
        @manage-users="handleSettingsManageUsers"
        @label-sessions="handleSettingsLabelSessions"
        @edit-projects="handleSettingsEditProjects"
        @download-backup="handleDownloadBackup"
        @restore-backup="handleRestoreBackup"
        @profile="openProfile"
        @logout="handleLogout"
      />

      <!-- Admin: ManageProjectsView is the permanent base, never
           unmounted; at most one view is ever pushed over it. Every push
           target 2D-slides except chat, which 3D-flips. -->
      <template v-else-if="currentUserRole === 'admin'">
        <ManageProjectsView
          ref="manageProjectsView"
          class="view-flip-base"
          :class="{
            'view-flip-base-flipped': pushedView === 'chat',
            'view-flip-base-forward': navDirection === 'forward',
            'view-flip-base-back': navDirection === 'back'
          }"
          :uploading="uploadingProject"
          :upload-progress="uploadProgress"
          :upload-project-name="uploadProjectName"
          :upload-icon-ready="uploadIconReady"
          role="admin"
          :profile="currentUserProfile"
          @new-project="handleNewProject"
          @upload="triggerModelUpload"
          @delete="handleModelDelete"
          @edit="handleModelEdit"
          @label="handleSelectLabelSessions"
          @chat="handleManageProjectsChat"
          @download="handleModelDownload"
          @wipe-live-sessions="handleManageProjectsWipeLiveSessions"
          @manage-users="handleSettingsManageUsers"
          @label-sessions="handleSettingsLabelSessions"
          @edit-projects="handleSettingsEditProjects"
          @about="handleShowAbout"
          @download-backup="handleDownloadBackup"
          @restore-backup="handleRestoreBackup"
          @profile="openProfile"
          @logout="handleLogout"
        />

        <Transition :name="slideTransitionName">
          <EditProjectView
            v-if="pushedView === 'edit'"
            :key="editProjectName"
            :project-name="editProjectName"
            role="admin"
            :profile="currentUserProfile"
            @saved="handleModelEditSaved"
            @back="popPushedView"
            @project-select="handleModelEdit"
            @manage-users="handleSettingsManageUsers"
            @label-sessions="handleSettingsLabelSessions"
            @edit-projects="handleSettingsEditProjects"
            @download-backup="handleDownloadBackup"
            @restore-backup="handleRestoreBackup"
            @profile="openProfile"
            @logout="handleLogout"
          />
          <LabelProjectView
            v-else-if="pushedView === 'label'"
            :key="labelProjectName"
            :project-name="labelProjectName"
            role="admin"
            :profile="currentUserProfile"
            @close="popPushedView"
            @project-select="handleLabelProjectSwitch"
            @manage-users="handleSettingsManageUsers"
            @label-sessions="handleSettingsLabelSessions"
            @edit-projects="handleSettingsEditProjects"
            @download-backup="handleDownloadBackup"
            @restore-backup="handleRestoreBackup"
            @profile="openProfile"
            @logout="handleLogout"
          />
          <ManageUsersView
            v-else-if="pushedView === 'manageUsers'"
            :current-user-role="currentUserRole"
            :profile="currentUserProfile"
            @close="popPushedView"
            @manage-users="handleSettingsManageUsers"
            @label-sessions="handleSettingsLabelSessions"
            @edit-projects="handleSettingsEditProjects"
            @download-backup="handleDownloadBackup"
            @restore-backup="handleRestoreBackup"
            @profile="openProfile"
            @logout="handleLogout"
          />
        </Transition>

        <Transition
          :css="false"
          @before-enter="onChatBeforeEnter"
          @enter="onChatEnter"
          @before-leave="onChatBeforeLeave"
          @leave="onChatLeave"
        >
          <LiveChatWindow
            v-if="pushedView === 'chat'"
            ref="chatWindowRef"
            :project-name="liveChatProjectName"
            :role="currentUserRole"
            :profile="currentUserProfile"
            @project-select="handleLiveChatProjectSelect"
            @project-download="handleModelDownload"
            @manage-projects="handleSettingsManageProjects"
            @profile="openProfile"
            @logout="handleLogout"
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

    <!-- Universal, independent of the role/stack above: reachable from
         any of the 3 branches via the same ProfileMenu. -->
    <Transition :name="slideTransitionName">
      <ProfileView
        v-if="showProfile"
        @close="() => { setNavBack(); showProfile = false }"
      />
    </Transition>

  </div>
</template>

<style>
html,
body {
  margin: 0;
  padding: 0;
  /* Plain 100%, not calc(100% + overshoot) — an earlier attempt at
     extending this box past the viewport to cover the standalone-iOS
     gap (see --viewport-bottom-overshoot) didn't work: the background
     a canvas-less document propagates to the canvas is positioned
     against the *viewport* (the initial containing block), not against
     html's own box, so stretching html/body's own height never actually
     moved where that painted region ends. It also had a real side
     effect worth undoing on its own — a root taller than the viewport
     is genuine scrollable overflow. App.vue's own .app-backdrop (see
     its template, first element, and its own style below) replaces this
     as the thing that actually covers the gap; the background here
     stays only as the last-resort fallback behind that real element. */
  height: 100%;
  /* Not overflow: hidden — that risked clipping the full-viewport
     containers' own bottom overshoot (see .live-chat-window's own
     comment and --viewport-bottom-overshoot) at the compositing level.
     touch-action/overscroll-behavior-y below already do the two jobs
     overflow: hidden existed for (blocking scroll and rubber-band), and
     html/body still can't actually scroll on their own: both are sized
     to exactly the viewport's own height, and the fixed, overshot
     containers don't generate scrollable overflow. */
  /* Belt-and-suspenders against Android Chrome's pull-to-refresh — the
     live chat transcript itself is the primary fix (see ChatView.vue's
     .messages), this just stops the same rubber-band reaching the body
     from any other edge case. */
  overscroll-behavior-y: none;
  /* Blocks pinch-zoom and double-tap-zoom app-wide — the viewport meta
     tag's own maximum-scale/user-scalable is the "classic" way to do
     this, but iOS Safari has deliberately ignored it since iOS 10 (an
     accessibility change); touch-action is still honored there, and,
     unlike the meta tag, applies via ancestor intersection — one rule
     here covers every screen (splash, login, terms, chat, admin views)
     without needing a matching rule on each. pan-x pan-y, not
     manipulation: per spec manipulation is shorthand for "pan-x pan-y
     pinch-zoom" — it explicitly *keeps* pinch-zoom enabled (only drops
     double-tap-zoom), the opposite of what's wanted here. Listing just
     the two pan values allows normal single-finger panning/scrolling
     and tapping (see each scrollable element's own overflow, e.g.
     ChatView.vue's .messages) while leaving both zoom gestures out. */
  touch-action: pan-x pan-y;
  /* Shows around .app's edges once it shrinks for an open dialog (see
     .app-dialog-open) and through the chat flip's crossover (.app-body
     and .app are otherwise transparent) — one shared background instead
     of a dedicated div, since both need the exact same reveal. Also the
     base behind LoginView/TermsView/SplashScreen (each references this
     same custom property, defined here since it's the only truly global
     stylesheet in the app). */
  --app-base-gradient: linear-gradient(160deg, #e4e7eb, #9aa1ac);
  /* Trailing #9aa1ac (not just the gradient alone) sets background-color
     within the same shorthand — the background shorthand resets any
     sub-property it doesn't mention back to its initial value, so a
     separate background-color declaration before or after this one would
     just get overwritten/ignored, not layered with it. WebKit paints any
     area outside every element's own box (e.g. a viewport stuck short by
     the standalone-webapp bug useVisualViewport.js works around) with the
     document canvas's own background-color, which was never set at all
     before this: default transparent, showing as stark white. #9aa1ac is
     --app-base-gradient's own darker end, so if that ever shows through
     for a moment it reads as part of the gradient, not as a gap.
     no-repeat: background-repeat defaults to repeat, which tiled a second
     copy of the gradient — starting over at its own light end, #e4e7eb —
     directly below this box whenever the height above didn't quite reach
     far enough on its own; harmless now that height accounts for the
     overshoot too, but there's no reason to leave a repeating background
     on a single-viewport box regardless. */
  background: var(--app-base-gradient) no-repeat #9aa1ac;
  /* One shared source for the four safe-area insets (notch/Dynamic
     Island, home indicator, rounded corners in landscape) — every
     top-level screen's own header/footer reserves space with
     calc(<its own base spacing> + var(--safe-area-*)) instead of each
     repeating env(safe-area-inset-*) directly. Same reasoning as
     --app-base-gradient above: defined once, here, since this is the
     one truly global stylesheet, so every screen stays in sync with
     whichever screen was fixed most recently instead of drifting apart
     the way ChatView.vue's .chat-header and
     ManageProjectsView.vue's .manage-projects-header did before this.
     Plain env() — index.html's own viewport meta keeps viewport-fit=cover,
     so these read real values; the WebKit bug that token has on the
     *bottom* edge in standalone mode is compensated separately, via each
     full-viewport container's own --viewport-bottom-overshoot (see
     useVisualViewport.js's installViewportOvershoot() and index.html's
     own viewport meta comment) rather than by faking these insets. */
  --safe-area-top: env(safe-area-inset-top);
  --safe-area-right: env(safe-area-inset-right);
  --safe-area-bottom: env(safe-area-inset-bottom);
  --safe-area-left: env(safe-area-inset-left);
}

#app {
  height: 100%;
}
</style>

<style scoped>
/* Replaces the html/body background as the backdrop a dialog reveals
   scaling .app down (.app-dialog-open), the chat's own 3D flip reveals
   mid-crossover, and Splash/Login/Terms sit in front of — those are all
   real elements with their own box, so unlike html/body's background
   (see that rule's own comment for why that one can't reach past the
   viewport on standalone iOS) this one's bottom can actually extend into
   --viewport-bottom-overshoot the same way every other full-viewport
   container already does. z-index: -1 keeps it under all real content
   while still painting above html/body's own canvas-propagated
   background. pointer-events: none — it's decorative only, never meant
   to intercept a tap/click meant for whatever's in front of it.

   Convention for every full-viewport surface (see useVisualViewport.js's
   own installViewportOvershoot() for where the variable itself comes
   from): extend its own bottom with
   calc(-1 * var(--viewport-bottom-overshoot, 0px)), same as here. Its
   root must never receive transform, filter, will-change: transform, or
   an animated opacity — any of those give a position: fixed element its
   own compositing layer, which WebKit clips to the (short, on standalone
   iOS) viewport regardless of the extended bottom, silently discarding
   it; put enter/leave animations on an inner content element instead
   (this file's own Splash/TermsView do — see .splash-content/
   .terms-panel). A top-layer element's own ::backdrop pseudo-element
   needs the same bottom extension applied explicitly (see DialogHost.vue's
   own .app-dialog::backdrop) — it's UA-styled viewport-sized on its own
   and isn't reachable through this element's box at all. */
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
  /* 100vh, not 100dvh: iOS has an active, confirmed bug in recent
     releases (see the Apple Developer Forums, "New IOS Safari CSS Issue
     with DVH & VH") where 100dvh leaves a gap at the bottom instead of
     covering the full screen — visible mainly in a standalone home-screen
     webapp like this one, which has no browser toolbar to dynamically
     shrink for in the first place, so dvh's whole reason to exist over
     vh doesn't even apply here. The real fix for the bottom gap is
     useVisualViewport.js's installViewportRecovery() — see its own
     comment — since the actual cause is a stuck-shrunk layout viewport
     after the keyboard's first use, which no CSS unit can see past. */
  height: 100vh;
  font-family: system-ui, -apple-system, sans-serif;
  /* none/none, not scale(1)/blur(0): those compute to the same visuals
     but (per spec) still establish a containing block for position:fixed
     descendants — LiveChatWindow.vue's/ManageProjectsView.vue's/
     SplashScreen.vue's own full-viewport position:fixed;inset:0 would
     then resolve against *this* box's own 100vh instead of the true
     viewport directly, inheriting whatever measurement quirk affects vh
     units in a standalone home-screen webapp on this iOS version instead
     of whatever (probably more reliable) code path the engine uses to
     size position:fixed against the real viewport. Only .app-dialog-open
     below needs the real transform, and only while a dialog is actually
     open. */
  transform: none;
  filter: none;
  transition: transform 0.2s ease-in-out, filter 0.2s ease-in-out;
}

/* The native <dialog> DialogHost.vue renders into sits in the browser's
   own top-layer, entirely unaffected by transforms on regular ancestors
   — so scaling/blurring .app here never touches the dialog's own size. */
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

/* perspective (any value but none) establishes a containing block for
   position:fixed descendants, same as .app's own transform/filter above
   — LiveChatWindow.vue's plain-user .live-chat-window (position:fixed;
   inset:0) would then resolve against *this* box instead of the true
   viewport. Scoped to the admin role, the only one that ever renders
   the 3D-flipping subtree below (.view-flip-base / the pushed 'chat'
   overlay) and so the only one that actually needs it. */
.app-body-flip-space {
  perspective: 1300px;
}

/* iOS-style 3D flip — chat only (see .app-body's perspective above).
   ManageProjectsView's own side of it (.view-flip-base below) is a plain
   reactive class toggle; chat's side is driven by JS transition hooks in
   the script instead of Vue's CSS-class Transition convention (see
   onChatBeforeEnter/onChatEnter/onChatBeforeLeave/onChatLeave and the
   chat <Transition>'s :css="false") — a Transition whose :name changes in
   the same tick as its child's v-if does not reliably re-resolve its
   enter/leave CSS classes against the new name on an already-mounted
   child, so chat's own leave kept using stale "forward" values after a
   pop tagged 'back'. JS hooks read navDirection fresh at call time and
   sidestep that entirely. */

/* ManageProjectsView is never unmounted (see the admin branch above), so
   it never gets Vue Transition enter/leave classes of its own — this is
   the same 0->90 leaving rotation as .view-flip-forward-leave-to /
   .view-flip-back-leave-to above, just driven by plain reactive classes
   instead, since there's no mount/unmount here to hook a Transition onto.
   navDirection-scoped because a single class toggle can't otherwise carry
   different timing for "rotating away" (push) vs "rotating back into
   view" (pop) on the very same transform property. */
.view-flip-base {
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

/* 2D push/pop slide — the other 5 top-level overlay views (Edit project,
   Label sessions, Manage projects, Manage users, Profile). Same
   forward/back semantics as the flip above, just a translateX instead of
   a rotateY. */
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

.upload-model-input {
  display: none;
}

</style>
