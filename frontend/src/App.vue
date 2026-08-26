<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ChatWindow from './components/chat/ChatWindow.vue'
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
  getMe,
  putProject,
  postNewProject,
  activateProject,
  deleteProject,
  postWipeLiveSessions,
  downloadProject,
  getBackup,
  postRestoreBackup,
  postPublishProject,
  postLogout,
  postAcceptTerms,
  getAbout
} from './api.js'
import { disconnect as disconnectChat } from './chatClient.js'
import { clearApiError } from './errorStore.js'
import { needsLogin, requireLogin } from './authStore.js'
import { roleSatisfies } from './roles.js'
import { confirmDialog, infoDialog } from './dialogStore.js'
import {
  setCapabilities,
  handleStateChange,
  loadMessages,
  loadAiModels,
  clearChatUi,
  currentProjectName,
  sessionsPanelOpen
} from './chatStore.js'

const showEditProject = ref(false)
const editProjectName = ref(null)
const showBenchmarkProject = ref(false)
const benchmarkProjectName = ref(null)
const showManageProjects = ref(false)
const showManageUsers = ref(false)
const showProfile = ref(false)
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

// Initial-boot backend readiness gate — entirely separate from the shared
// error store (which is for runtime errors on an already-running app). 'checking': the
// very first, invisible ping attempt (no splash yet, so a backend that's
// already up never flashes one). 'waiting': the first attempt failed,
// retrying on an interval with the splash visible. 'ready': normal app UI.
// 'failed': retry budget exhausted, explicit error + manual "Retry".
const bootStatus = ref('checking')
// True for a session that authenticated but has no User row — either a
// brand-new identity that never finished registration, or one whose row
// was deleted after the cookie was issued (see auth_service.py's own
// verify_token: both resolve to role=None, indistinguishable from here).
// TermsView.vue's Accept calls complete_registration(), which creates the
// row either way — this is the only path that recovers either case; a
// plain re-login just reissues the same role=None token. Takes over the
// whole screen the same way needsLogin does, ahead of bootStatus.
const needsTerms = ref(false)

const PING_INTERVAL_MS = 800
const PING_TIMEOUT_MS = 3000
const MAX_PING_ATTEMPTS = 30

// Boot-ping bookkeeping. `bootSequenceToken` is bumped by startBootSequence()
// so a stale scheduled retry from a previous sequence (e.g. right after the
// user clicks "Retry") can recognize it's been superseded and no-op instead
// of racing the fresh one.
let pingAttempts = 0
let pingTimeoutHandle = null
let bootSequenceToken = 0

// One ping attempt, bounded by an explicit timeout — plain fetch() never
// times out on its own against a hung connection, and "timeout" is one of
// the failure modes this boot check needs to treat the same as "not ready
// yet". On success, reuses the result directly as the app's current state
// (GET /api/state IS the readiness check — nothing else to fetch for it).
// 'ready': backend is up and this session is a fully registered user.
// 'pending': backend is up, but this session authenticated without a
// matching User row — GET /api/state requires role="user", which a
// pending (role=None) identity's 403 is the only way to reach here (see
// auth_service.py's own verify_token). Never "still booting": retrying
// with the same cookie always gets the same 403 back. 'retry': anything
// else (backend still starting, network hiccup, timeout).
async function pingBackend() {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), PING_TIMEOUT_MS)
  try {
    const newState = await getState(controller.signal)
    setCapabilities({ talkAvailable: newState.talk_enabled ?? true, micAvailable: newState.listen_enabled ?? true })
    handleStateChange(newState)
    return 'ready'
  } catch (err) {
    return err.status === 403 ? 'pending' : 'retry'
  } finally {
    clearTimeout(timeout)
  }
}

function bootSucceeded() {
  bootStatus.value = 'ready'
  // Clears any error left over from a failed boot-ping retry — that
  // retry loop is invisible UI (see pingBackend), but it goes through the
  // same apiFetch as everything else, so a stale message could otherwise
  // still be sitting in the shared store the moment the chat UI mounts.
  clearApiError()
  // loadMessages() is what actually creates/resolves the live session
  // (see chatStoreFactory.js's ensureSession) — only the chat-live landing
  // (a plain user, or a supervisor with no active project yet) needs one
  // at boot. An admin landing on Manage projects or a supervisor landing
  // on Label sessions (see goToLandingView, just run by resolveLandingView
  // before this) shouldn't spin up a live session nobody's about to use;
  // ChatWindow.vue stays mounted-but-hidden behind those and only needs
  // one once they actually switch into the live chat themselves (see
  // handleManageProjectsChat -> handleProjectSwitch -> refreshStateAndProjects).
  if (!showManageProjects.value && !showBenchmarkProject.value) {
    loadMessages()
  }
  loadAiModels()
  // No proactive chat-socket connect here: chatClient.js connects lazily
  // on the first sendMessage() call, and the opening message (if any) is
  // already covered by loadMessages() above — it's persisted server-side
  // by the time the backend finishes booting, regardless of transport.
}

// Every top-level full-screen view, off — the common first step of both
// picking a fresh landing view and switching to a different one, so
// exactly one is ever showing regardless of which one was active before
// (Settings is now reachable from more than just the main chat screen —
// see goToLandingView/handleSettings* below).
function closeAllTopLevelViews() {
  showEditProject.value = false
  showBenchmarkProject.value = false
  showManageProjects.value = false
  showManageUsers.value = false
  showProfile.value = false
}

// The role-appropriate "home": chat-live for a plain user, Label
// sessions for a supervisor (against whichever project is currently
// active), Manage projects for an admin. Neither Label sessions nor
// Manage projects has a Back button of its own any more, so this is also
// where Settings' own modal-like Manage users returns to on close.
function goToLandingView() {
  closeAllTopLevelViews()
  if (currentUserRole.value === 'admin') {
    showManageProjects.value = true
  } else if (currentUserRole.value === 'supervisor' && currentProjectName.value) {
    handleModelBenchmark(currentProjectName.value)
  }
  // Anything else (a plain 'user', or a supervisor with no active
  // project yet): the default chat-live landing, nothing further to set.
}

// Resolved once per boot, before bootStatus ever flips to 'ready' — the
// landing view has to be right from the very first render, not settled a
// moment later once some async fetch resolves (that would flash the
// chat-live default first for every supervisor/admin).
async function resolveLandingView() {
  try {
    currentUserProfile.value = await getMe()
    currentUserRole.value = currentUserProfile.value?.role ?? null
  } catch {
    return // already surfaced via apiFetch; falls back to the chat-live default
  }
  goToLandingView()
}

async function runPingAttempt(token) {
  if (token !== bootSequenceToken) return // superseded by a newer sequence
  pingAttempts++
  const result = await pingBackend()
  if (token !== bootSequenceToken) return
  if (result === 'ready') {
    await resolveLandingView()
    if (token !== bootSequenceToken) return
    bootSucceeded()
    return
  }
  if (result === 'pending') {
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

// Entry point for both the initial mount and the splash's manual "Retry" —
// restarts the exact same cycle: one immediate, invisible attempt, then
// (only if that one fails) the visible retry loop.
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

// LoginView.vue's own 'logged-in' — the session cookie is set, so the
// exact same startup path a fresh page load takes now succeeds instead
// of 401ing.
function handleLoggedIn() {
  startBootSequence()
}

// TermsView.vue's Accept — creates the User row (or recreates a deleted
// one, see needsTerms's own comment), then resumes booting exactly like
// a fresh login would: the same cookie now resolves as a registered user.
async function handleTermsAccept() {
  try {
    await postAcceptTerms()
  } catch {
    // already surfaced via apiFetch; stays on TermsView so it can be retried
    return
  }
  needsTerms.value = false
  startBootSequence()
}

// TermsView.vue's Reject — same clean logout as handleLogout, but no
// User row was ever created (or recreated), so this leaves no trace of
// the attempt.
async function handleTermsReject() {
  try {
    await postLogout()
  } catch {
    // already surfaced via apiFetch
  }
  disconnectChat()
  needsTerms.value = false
  requireLogin()
}

async function handleLogout() {
  try {
    await postLogout()
  } catch {
    // already surfaced via apiFetch
  }
  disconnectChat()
  requireLogin()
}

function triggerModelUpload() {
  modelUploadInput.value?.click()
}

// The fetch-fresh-state-and-redisplay half of the reset/switch/upload/
// delete flow (see chatStore.js's clearChatUi for the optimistic-clear
// half each of those runs first): the fresh state comes from a separate
// GET /api/state call (none of putModel/activateModel/deleteModel's
// responses carry the state payload itself), same as handleReset picks up
// the opening message via REST, regardless of chat transport.
// `skipMessages`: EditProjectView.vue's own two callers (entering and
// publishing-while-still-inside it) pass this — Edit always hides
// ChatWindow.vue (v-if="!showEditProject") and Back returns to Manage
// projects, never to the live chat, so loadMessages() here would only
// ever create a live session nobody's about to see. Worse than wasted:
// ChatWindow.vue stays mounted (just hidden) while inside Edit, so a
// legalTermsPending it set would still be sitting there the moment Back
// remounts it visibly — and TermsView.vue's z-index (1000) sits above
// Manage projects' own overlay (100), so that would show as a stuck
// full-screen Terms prompt blocking Manage projects, not just a flash.
async function refreshStateAndProjects({ skipMessages = false } = {}) {
  const newState = await getState()
  chatWindowRef.value?.refreshProjectsMenu()
  manageProjectsView.value?.refresh()
  handleStateChange(newState)
  if (!skipMessages) await loadMessages()
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
    await refreshStateAndProjects({ skipMessages: true })
  } catch {
    // already surfaced via apiFetch
  }
  editProjectName.value = projectName
  showEditProject.value = true
}

function handleManageProjectsEdit(projectName) {
  showManageProjects.value = false
  handleModelEdit(projectName)
}

function handleManageProjectsBenchmark(projectName) {
  showManageProjects.value = false
  handleModelBenchmark(projectName)
}

// "Open chat" on a project's own row: same switch as picking it from
// ProjectsMenu, then back to the main chat screen (closing Manage
// projects is what actually reveals it — there's no separate route).
function handleManageProjectsChat(projectName) {
  showManageProjects.value = false
  handleProjectSwitch(projectName)
}

function handleModelBenchmark(projectName) {
  benchmarkProjectName.value = projectName
  showBenchmarkProject.value = true
}

// Settings' own three view-switching entries — each closes whichever
// top-level view is currently showing first, since Settings is now
// reachable from all of them (main chat, Label sessions, Manage
// projects), not just the main chat screen.
function handleSettingsManageProjects() {
  closeAllTopLevelViews()
  showManageProjects.value = true
}

function handleSettingsManageUsers() {
  closeAllTopLevelViews()
  showManageUsers.value = true
}

// Opened straight at whichever project is currently active, rather than
// via Manage projects.
function handleSettingsLabelSessions() {
  if (!currentProjectName.value) return
  closeAllTopLevelViews()
  handleModelBenchmark(currentProjectName.value)
}

// Settings-menu shortcut straight into Edit for whichever project is
// currently active — same shape as handleSettingsLabelSessions above.
function handleSettingsEditProjects() {
  if (!currentProjectName.value) return
  closeAllTopLevelViews()
  handleModelEdit(currentProjectName.value)
}

async function handleModelEditSaved() {
  clearChatUi()
  try {
    await refreshStateAndProjects({ skipMessages: true })
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
  } catch {
    // already surfaced via apiFetch
  }
}

// LabelProjectView's own ProjectsMenu, for switching project without
// leaving the label view. Reuses the same activation as a normal switch,
// then repoints the view at the new project — its :key below remounts it,
// same as opening it fresh from Manage projects.
async function handleBenchmarkProjectSwitch(projectName) {
  await handleProjectSwitch(projectName)
  benchmarkProjectName.value = projectName
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
    await infoDialog({ title: about.name, body: `Version ${about.version}` })
  } catch {
    // already surfaced via apiFetch
  }
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

  <div v-else-if="bootStatus === 'ready'" class="app">
    <ErrorBanner />

    <div class="app-body">
      <ChatWindow
        v-if="!showEditProject"
        ref="chatWindowRef"
        @project-select="handleProjectSwitch"
        @project-download="handleModelDownload"
      />

      <div class="topbar-overlay" :class="{ 'topbar-overlay-hidden': sessionsPanelOpen }">
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
        <ProfileMenu :profile="currentUserProfile" @profile="showProfile = true" @logout="handleLogout" />
      </div>

      <input
        ref="modelUploadInput"
        type="file"
        accept=".zip,.yml,.yaml"
        class="upload-model-input"
        @change="handleModelUploadChange"
      />
    </div>

    <EditProjectView
      v-if="showEditProject"
      :key="editProjectName"
      :project-name="editProjectName"
      :role="currentUserRole"
      :profile="currentUserProfile"
      @saved="handleModelEditSaved"
      @project-select="handleModelEdit"
      @manage-projects="handleSettingsManageProjects"
      @manage-users="handleSettingsManageUsers"
      @label-sessions="handleSettingsLabelSessions"
      @edit-projects="handleSettingsEditProjects"
      @about="handleShowAbout"
      @download-backup="handleDownloadBackup"
      @restore-backup="handleRestoreBackup"
      @profile="showProfile = true"
      @logout="handleLogout"
    />

    <LabelProjectView
      v-if="showBenchmarkProject"
      :key="benchmarkProjectName"
      :project-name="benchmarkProjectName"
      :role="currentUserRole"
      :profile="currentUserProfile"
      @close="goToLandingView"
      @project-select="handleBenchmarkProjectSwitch"
      @manage-projects="handleSettingsManageProjects"
      @manage-users="handleSettingsManageUsers"
      @label-sessions="handleSettingsLabelSessions"
      @edit-projects="handleSettingsEditProjects"
      @about="handleShowAbout"
      @download-backup="handleDownloadBackup"
      @restore-backup="handleRestoreBackup"
      @profile="showProfile = true"
      @logout="handleLogout"
    />

    <ManageProjectsView
      v-if="showManageProjects"
      ref="manageProjectsView"
      :uploading="uploadingProject"
      :upload-progress="uploadProgress"
      :role="currentUserRole"
      :profile="currentUserProfile"
      @new-project="handleNewProject"
      @upload="triggerModelUpload"
      @delete="handleModelDelete"
      @edit="handleManageProjectsEdit"
      @benchmark="handleManageProjectsBenchmark"
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
      @profile="showProfile = true"
      @logout="handleLogout"
    />

    <ManageUsersView
      v-if="showManageUsers"
      :current-user-role="currentUserRole"
      :profile="currentUserProfile"
      @close="goToLandingView"
      @manage-projects="handleSettingsManageProjects"
      @manage-users="handleSettingsManageUsers"
      @label-sessions="handleSettingsLabelSessions"
      @edit-projects="handleSettingsEditProjects"
      @about="handleShowAbout"
      @download-backup="handleDownloadBackup"
      @restore-backup="handleRestoreBackup"
      @profile="showProfile = true"
      @logout="handleLogout"
    />

    <ProfileView
      v-if="showProfile"
      @close="showProfile = false"
    />

  </div>
</template>

<style>
html,
body {
  margin: 0;
  padding: 0;
  height: 100%;
  overflow: hidden;
}

#app {
  height: 100%;
}
</style>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  font-family: system-ui, -apple-system, sans-serif;
}

.app-body {
  position: relative;
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
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
  z-index: 30;
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
