<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import LiveChatWindow from './components/chat/LiveChatWindow.vue'
import EditProjectView from './components/project/edit/EditProjectView.vue'
import LabelProjectView from './components/project/label/LabelProjectView.vue'
import LoginView from './components/LoginView.vue'
import TermsView from './components/TermsView.vue'
import ProfileMenu from './components/ProfileMenu.vue'
import ProfileView from './components/ProfileView.vue'
import SettingsMenu from './components/settings/SettingsMenu.vue'
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
import { roleSatisfies } from './roles.js'
import { activeDialog, aboutDialog, confirmDialog } from './dialogStore.js'
import {
  handleStateChange,
  loadMessages,
  clearChatUi,
  sessionsPanelOpen
} from './chatStore.js'
import { useAppBoot } from './composables/useAppBoot.js'
import { useChatFlipTransition } from './composables/useChatFlipTransition.js'
import { useVisualViewport } from './composables/useVisualViewport.js'

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

// Not consumed directly here — calling it activates the shared listener
// that mirrors window.visualViewport onto <html>'s own CSS custom
// properties (see the composable), which .app's own height below and
// LiveChatWindow.vue's positioning both read.
useVisualViewport()

const { onChatBeforeEnter, onChatEnter, onChatBeforeLeave, onChatLeave } = useChatFlipTransition(navDirection)
// Chat has no Settings/Profile controls of its own (unlike the other
// views, each of which builds SettingsMenu/ProfileMenu into its own
// header) — true whenever chat is the thing on screen, whether that's a
// plain user's whole app or an admin's pushed chat, so the shared
// .topbar-overlay below knows when to show itself.
const chatVisible = computed(() => currentUserRole.value === 'user' || pushedView.value === 'chat')
const dialogOpen = computed(() => !!activeDialog.value)
const modelUploadInput = ref(null)
const chatWindowRef = ref(null)
const manageProjectsView = ref(null)
const uploadingProject = ref(false)
// 0-100, or null before the first progress chunk has arrived — see
// SessionsTree.vue's identical importProgress for the same reasoning.
const uploadProgress = ref(null)
// Fetched once, up front (see resolveLandingView) — the role-based
// landing routing needs it before the very first render, and ProfileMenu.vue's
// own avatar reuses the same fetch instead of a second, redundant
// /api/auth/me call (see its own `profile` prop below).
const currentUserProfile = ref(null)
const currentUserRole = ref(null)

const {
  bootStatus, needsTerms,
  getActiveProjectName, startBootSequence,
  handleLoggedIn, handleTermsAccept, handleTermsReject, handleLogout,
} = useAppBoot(
  currentUserProfile, currentUserRole, labelProjectName, liveChatProjectName,
  pushedView, showProfile, navDirection
)

function triggerModelUpload() {
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
  try {
    await putProject(projectName, file, (message) => {
      uploadProgress.value = message.percentage
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

// Settings' own three view-switching entries. Manage projects is the
// admin's permanent base (never unmounted) — picking it is always a pop,
// never a push. The other two are admin-only push targets, but this is
// also reachable by a supervisor clicking their own "Label sessions" item
// (already where they are, since it's their whole app) — for them this
// just re-points their standing LabelProjectView at the current active
// project, with no push/transition at all.
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

onMounted(startBootSequence)
onBeforeUnmount(() => {
  disconnectChat()
  if (pingTimeoutHandle) clearTimeout(pingTimeoutHandle)
})
</script>

<template>
  <!-- 'checking' (the invisible first ping) renders neither branch, on
       purpose: nothing should flash before we know whether the backend was
       already up. -->
  <ToastContainer />
  <DialogHost />

  <!-- Overrides everything below regardless of bootStatus — a 401 (see
       api.js's apiFetch) can happen at any point, including mid-boot. -->
  <LoginView v-if="needsLogin" @logged-in="handleLoggedIn" />

  <TermsView v-else-if="needsTerms" @accept="handleTermsAccept" @reject="handleTermsReject" />

  <SplashScreen v-else-if="bootStatus === 'waiting'" variant="connecting" />
  <SplashScreen v-else-if="bootStatus === 'failed'" variant="failed" @retry="startBootSequence" />

  <div v-else-if="bootStatus === 'ready'" class="app" :class="{ 'app-dialog-open': dialogOpen }">
    <ErrorBanner />

    <div class="app-body">
      <!-- Plain user: chat is the entire app, no stack, no transition. -->
      <LiveChatWindow
        v-if="currentUserRole === 'user'"
        ref="chatWindowRef"
        :project-name="liveChatProjectName"
        @project-select="handleLiveChatProjectSelect"
        @project-download="handleModelDownload"
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
        @manage-projects="handleSettingsManageProjects"
        @manage-users="handleSettingsManageUsers"
        @label-sessions="handleSettingsLabelSessions"
        @edit-projects="handleSettingsEditProjects"
        @about="handleShowAbout"
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
          @manage-projects="handleSettingsManageProjects"
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
            @manage-projects="handleSettingsManageProjects"
            @manage-users="handleSettingsManageUsers"
            @label-sessions="handleSettingsLabelSessions"
            @edit-projects="handleSettingsEditProjects"
            @about="handleShowAbout"
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
            @manage-projects="handleSettingsManageProjects"
            @manage-users="handleSettingsManageUsers"
            @label-sessions="handleSettingsLabelSessions"
            @edit-projects="handleSettingsEditProjects"
            @about="handleShowAbout"
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
            @manage-projects="handleSettingsManageProjects"
            @manage-users="handleSettingsManageUsers"
            @label-sessions="handleSettingsLabelSessions"
            @edit-projects="handleSettingsEditProjects"
            @about="handleShowAbout"
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
            @project-select="handleLiveChatProjectSelect"
            @project-download="handleModelDownload"
          />
        </Transition>
      </template>

      <div class="topbar-overlay" v-if="chatVisible" :class="{ 'topbar-overlay-hidden': sessionsPanelOpen }">
        <SettingsMenu
          v-if="roleSatisfies(currentUserRole, 'supervisor')"
          :role="currentUserRole"
          align="right"
          @manage-projects="handleSettingsManageProjects"
          @manage-users="handleSettingsManageUsers"
          @label-sessions="handleSettingsLabelSessions"
          @edit-projects="handleSettingsEditProjects"
          @about="handleShowAbout"
          @download-backup="handleDownloadBackup"
          @restore-backup="handleRestoreBackup"
        />
        <ProfileMenu :profile="currentUserProfile" @profile="openProfile" @logout="handleLogout" />
      </div>

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
  height: 100%;
  overflow: hidden;
  /* Belt-and-suspenders against Android Chrome's pull-to-refresh — the
     live chat transcript itself is the primary fix (see ChatView.vue's
     .messages), this just stops the same rubber-band reaching the body
     from any other edge case. */
  overscroll-behavior-y: none;
  /* Shows around .app's edges once it shrinks for an open dialog (see
     .app-dialog-open) and through the chat flip's crossover (.app-body
     and .app are otherwise transparent) — one shared background instead
     of a dedicated div, since both need the exact same reveal. Also the
     base behind LoginView/TermsView/SplashScreen (each references this
     same custom property, defined here since it's the only truly global
     stylesheet in the app). */
  --app-base-gradient: linear-gradient(160deg, #e4e7eb, #9aa1ac);
  background: var(--app-base-gradient);
}

#app {
  height: 100%;
}
</style>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  /* Not 100vh: that's the *layout* viewport, which doesn't shrink for
     the on-screen keyboard or a pinch-zoom — content past its edge was
     genuinely unreachable (html/body are overflow: hidden, and iOS
     doesn't reliably reset pageScale on blur). The custom property is
     window.visualViewport's own height, kept live by useVisualViewport()
     above; 100dvh is the fallback for a browser without that API. */
  height: var(--visual-viewport-height, 100dvh);
  font-family: system-ui, -apple-system, sans-serif;
  transform: scale(1);
  filter: blur(0);
  transition: transform 0.2s ease-in-out, filter 0.2s ease-in-out;
}

/* The native <dialog> DialogHost.vue renders into sits in the browser's
   own top-layer, entirely unaffected by transforms on regular ancestors
   — so scaling/blurring .app here never touches the dialog's own size. */
.app-dialog-open {
  transform: scale(0.9);
  filter: blur(3px);
}

.app-body {
  position: relative;
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
  perspective: 1300px;
  --flip-duration: 500ms;
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

/* Anchored to .app-body, not the viewport: when ErrorBanner pushes
   .app-body down, this must shift down with it. Settings sits left of
   Profile (row order in the template) — the two read as one control
   cluster in the corner, both hidden together (see -hidden below) since
   neither means anything once the sessions panel covers this corner. */
.topbar-overlay {
  position: absolute;
  top: 0.75rem;
  right: 0.75rem;
  z-index: 200;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  transition: transform 0.32s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.32s cubic-bezier(0.22, 1, 0.36, 1);
  will-change: transform, opacity;
  backface-visibility: hidden;
}

/* Slides out to the right in lockstep with the sessions panel opening
   (see ChatWindow.vue's own .chat-window-dimmed fade/blur). */
.topbar-overlay-hidden {
  transform: translateX(3.5rem);
  opacity: 0;
  pointer-events: none;
}

.upload-model-input {
  display: none;
}

</style>
