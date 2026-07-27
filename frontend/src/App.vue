<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import ChatWindow from './components/ChatWindow.vue'
import StateBar from './components/StateBar.vue'
import ActionButtons from './components/ActionButtons.vue'
import SignalsView from './components/SignalsView.vue'
import EditProjectView from './components/EditProjectView.vue'
import ProjectsMenu from './components/ProjectsMenu.vue'
import SplashScreen from './components/SplashScreen.vue'
import {
  getState,
  getMessages,
  postAction,
  getAutoTracking,
  postAutoTracking,
  messageAudioUrl,
  postListenTranscribe,
  postReset,
  putProject,
  activateProject,
  deleteProject,
  downloadProject
} from './api.js'
import { disconnect as disconnectChat, sendMessage } from './chatClient.js'
import { playMessageChime, playMessageAudio } from './audio.js'
import { celebrate } from './confetti.js'
import { clearApiError, errorDetail, errorMessage, setApiError } from './errorStore.js'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({
  breaks: true,    // invio = <br>
  linkify: true,   // URL cliccabili
  typographer: true
})

function renderMarkdown(text) {
  return DOMPurify.sanitize(md.render(text ?? ''))
}
const showSignals = ref(false)
const showEditProject = ref(false)
const editProjectName = ref(null)
const autoTrackingEnabled = ref(true)
const autoTrackingLoading = ref(false)
// Pure frontend state — the backend generates audio on demand whenever
// this is on, no persisted toggle server-side (see maybeAutoPlayAudio).
const audioEnabled = ref(false)
// Whether the server actually has talk-service/listen-service configured
// (see backend/.config.yml's `enabled`) — set once from the boot ping
// (see pingBackend) and never touched again: unlike `state`, this isn't
// per-turn data, so it must not be overwritten by a later chat/action
// response that doesn't carry these fields at all.
const talkAvailable = ref(true)
const micAvailable = ref(true)
// When on, assistant bubbles show audio_text (the short narrated phrase,
// see backend's [audio] tag) instead of the full reply — purely a display
// switch, no playback triggered (see toggleSpokenText).
const spokenTextEnabled = ref(false)
const state = ref(null)
const messages = ref([])
const historyLoaded = ref(false)
const chatLoading = ref(false)
const chatStatus = ref('')
const actionLoading = ref(false)
const modelUploadInput = ref(null)
const projectsMenu = ref(null)
const chatWindow = ref(null)
const signalsView = ref(null)

// Signals only ever change server-side as a result of auto-tracking inside
// a chat turn/action, or wholesale when the active model itself changes
// (reset/switch/upload/delete) — called from every one of those spots so
// the docked inspector (see SignalsView) tracks the backend live instead
// of only refreshing when reopened. A no-op while the panel is closed.
function refreshSignalsIfOpen() {
  if (showSignals.value) signalsView.value?.refresh()
}

// Draggable split between the chat and the docked Signals panel (see
// SignalsView's own >=900px breakpoint — the resizer itself is hidden
// below that width via the same media query, so this only ever matters
// in docked mode). Width persists across reloads; a fresh session with no
// saved width falls back to the panel's own default (400px, in its CSS).
const SIGNALS_MIN_WIDTH = 280
const SIGNALS_MAX_WIDTH = 720
const CHAT_MIN_WIDTH = 320
const appBody = ref(null)
const resizingSignals = ref(false)
const signalsPanelWidth = ref(Number(localStorage.getItem('signalsPanelWidth')) || 400)

function startSignalsResize(event) {
  resizingSignals.value = true
  event.target.setPointerCapture(event.pointerId)
}

function onSignalsResizeMove(event) {
  if (!resizingSignals.value || !appBody.value) return
  const rect = appBody.value.getBoundingClientRect()
  const maxWidth = Math.min(SIGNALS_MAX_WIDTH, rect.width - CHAT_MIN_WIDTH)
  const newWidth = rect.right - event.clientX
  signalsPanelWidth.value = Math.min(maxWidth, Math.max(SIGNALS_MIN_WIDTH, newWidth))
}

function stopSignalsResize(event) {
  if (!resizingSignals.value) return
  resizingSignals.value = false
  event.target.releasePointerCapture(event.pointerId)
  localStorage.setItem('signalsPanelWidth', String(signalsPanelWidth.value))
}

// Initial-boot backend readiness gate — entirely separate from the shared
// error store (which is for runtime errors on an already-running app). 'checking': the
// very first, invisible ping attempt (no splash yet, so a backend that's
// already up never flashes one). 'waiting': the first attempt failed,
// retrying on an interval with the splash visible. 'ready': normal app UI.
// 'failed': retry budget exhausted, explicit error + manual "Retry".
const bootStatus = ref('checking')

const PING_INTERVAL_MS = 800
const PING_TIMEOUT_MS = 3000
const MAX_PING_ATTEMPTS = 30

// Stable id assigned to every user message at creation, so its status can
// be found and updated by identity later — never by mutating whatever
// object reference the caller happened to capture (see submitMessage: a
// direct mutation on that captured reference bypasses Vue's reactive
// array proxy entirely, so the UI doesn't update until some unrelated
// change happens to force a re-render).
let nextMessageId = 0

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
async function pingBackend() {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), PING_TIMEOUT_MS)
  try {
    const newState = await getState(controller.signal)
    talkAvailable.value = newState.talk_enabled ?? true
    micAvailable.value = newState.listen_enabled ?? true
    handleStateChange(newState)
    return true
  } catch {
    return false
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
  loadMessages()
  loadAutoTracking()
  // No proactive chat-socket connect here: chatClient.js connects lazily
  // on the first sendMessage() call, and the opening message (if any) is
  // already covered by loadMessages() above — it's persisted server-side
  // by the time the backend finishes booting, regardless of transport.
}

async function runPingAttempt(token) {
  if (token !== bootSequenceToken) return // superseded by a newer sequence
  pingAttempts++
  const ok = await pingBackend()
  if (token !== bootSequenceToken) return
  if (ok) {
    bootSucceeded()
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

function handleStateChange(newState) {
  const changed = state.value?.key !== newState.key
  state.value = newState
  // Only on an actual transition into the state, never on a redundant
  // re-fetch of the one we're already in (e.g. the boot ping, or any other
  // GET /api/state call that happens to return the same state) — otherwise
  // celebrate() would refire every time this runs.
  if (changed && newState?.on_enter === 'celebrate') {
    celebrate()
  }
}

// Redisplays whatever conversation the backend already persisted (e.g.
// across a backend restart) — session.history server-side is otherwise only
// ever used internally to build LLM calls, never pushed to the client.
async function loadMessages() {
  try {
    const history = await getMessages()
    messages.value = history.map((m) => ({
      role: m.role,
      content: m.content,
      audioText: m.audio_text,
      failed: false,
      messageId: m.id
    }))
  } catch {
    // already surfaced via apiFetch
  } finally {
    // Gates ChatWindow's bump-in animation (see its historyLoaded prop):
    // this hydration is async, so it lands well after ChatWindow has
    // already mounted — without this flag every history row would still
    // read as "just added" the moment it arrives. Setting `messages` and
    // `historyLoaded` in the very same tick isn't enough on its own: Vue
    // batches both changes into one render, so TransitionGroup would see
    // the *new* name already in effect for the very update that adds the
    // history rows. Waiting a tick lets that first render (still gated by
    // the old, unstyled name) flush before the flag flips.
    await nextTick()
    historyLoaded.value = true
  }
}

async function loadAutoTracking() {
  try {
    const res = await getAutoTracking()
    autoTrackingEnabled.value = res.enabled
  } catch {
    // already surfaced via apiFetch
  }
}

async function toggleAutoTracking() {
  autoTrackingLoading.value = true
  try {
    const res = await postAutoTracking(!autoTrackingEnabled.value)
    autoTrackingEnabled.value = res.enabled
  } catch {
    // already surfaced via apiFetch
  } finally {
    autoTrackingLoading.value = false
  }
}

function toggleAudio() {
  audioEnabled.value = !audioEnabled.value
  if (audioEnabled.value) {
    const lastAssistant = [...messages.value].reverse().find((m) => m.role === 'assistant' && m.messageId != null)
    if (lastAssistant) playMessageAudio(messageAudioUrl(lastAssistant.messageId))
  }
}

// Purely a display switch — flipping it doesn't touch playback, only
// which text ChatWindow renders for assistant bubbles (see its
// spokenTextEnabled prop); reactivity alone re-renders every bubble.
function toggleSpokenText() {
  spokenTextEnabled.value = !spokenTextEnabled.value
}

// Fires the automatic narration for the last message a turn produced —
// same call regardless of which transport delivered it (see submitMessage
// / handleAction, the only two places a live message ever arrives) and a
// no-op if the toggle is off or the message has no id to look up.
function maybeAutoPlayAudio(messageId) {
  if (!audioEnabled.value || messageId == null) return
  playMessageAudio(messageAudioUrl(messageId))
}

// Looks the message back up by id through the reactive `messages` array
// (rather than mutating whatever reference the caller passed in) so the
// assignment goes through Vue's reactive proxy and updates the UI
// immediately — see the note by `nextMessageId` above.
function setMessageFailed(id, failed) {
  const target = messages.value.find((m) => m.id === id)
  if (target) target.failed = failed
}

async function submitMessage(message) {
  clearApiError()
  setMessageFailed(message.id, false)
  chatLoading.value = true
  try {
    // sendMessage() (chatClient.js) tries the websocket first and falls
    // back to REST transparently — this code never knows which one ran.
    const result = await sendMessage(message.content, {
      onStatus: (text) => { chatStatus.value = text }
    })
    // result.reply is an array of {id, content}: normally one bubble, but
    // a mid-turn auto-tracking transition into a fresh state can
    // prepend/append that state's own opening message alongside the
    // turn's own reply — one bubble per element, in the order the
    // backend produced them.
    for (const { id, content, audio_text } of result.reply) {
      messages.value.push({ role: 'assistant', content, audioText: audio_text, messageId: id })
    }
    // Only for a freshly arrived AI reply — never for the user's own sent
    // message, and never for history loaded at boot/reset (this only ever
    // runs from a live chat turn just completing).
    playMessageChime()
    // Narrates only the LAST bubble of the turn — only that one can have
    // an [audio] tag (see backend/chat/chat_service.py's _extract_audio_tag).
    if (result.reply.length) maybeAutoPlayAudio(result.reply[result.reply.length - 1].id)
    handleStateChange(result.state)
    refreshSignalsIfOpen()
  } catch {
    // Already surfaced via the websocket handler or apiFetch (see
    // chatClient.js) — this only has to update this specific message's
    // own status, synchronously with the outcome.
    setMessageFailed(message.id, true)
  } finally {
    chatLoading.value = false
    chatStatus.value = ''
  }
}

async function handleSend(text) {
  const message = { id: ++nextMessageId, role: 'user', content: text, failed: false }
  messages.value.push(message)
  await submitMessage(message)
}

// Removes the placeholder bubble outright (rather than marking it
// failed): with no transcribed text there's nothing a resend could
// retry, so the existing failed/resend affordance doesn't fit here.
function dropVoicePlaceholder(id) {
  const idx = messages.value.findIndex((m) => m.id === id)
  if (idx !== -1) messages.value.splice(idx, 1)
}

// The mic button's whole point: the transcribed text is never staged in
// `draft` for review — a user-side placeholder (same waiting style as the
// assistant's own, see ChatWindow's bubble-loading) stands in for it while
// transcription runs, then becomes the real sent message in place, same
// id and same failed/resend lifecycle as a typed one (see submitMessage).
async function handleVoiceMessage(audioBlob) {
  const message = { id: ++nextMessageId, role: 'user', content: '', failed: false, transcribing: true }
  messages.value.push(message)

  let text
  try {
    const result = await postListenTranscribe(audioBlob)
    text = result.text?.trim()
  } catch {
    // Already surfaced via apiFetch.
    dropVoicePlaceholder(message.id)
    return
  }
  if (!text) {
    dropVoicePlaceholder(message.id)
    return
  }

  message.content = text
  message.transcribing = false
  await submitMessage(message)
}

async function handleResend(index) {
  if (chatLoading.value) return
  const message = messages.value[index]
  if (!message || message.role !== 'user') return
  await submitMessage(message)
}

async function handleAction(actionName) {
  actionLoading.value = true
  try {
    // {state, reply}: reply is the destination state's own opening
    // message, same {id, content} array shape as a normal turn's (see
    // submitMessage) — empty if it already had something to say since
    // its own cutoff.
    const result = await postAction(actionName)
    for (const { id, content, audio_text } of result.reply) {
      messages.value.push({ role: 'assistant', content, audioText: audio_text, messageId: id })
    }
    if (result.reply.length) {
      playMessageChime()
      maybeAutoPlayAudio(result.reply[result.reply.length - 1].id)
    }
    handleStateChange(result.state)
    refreshSignalsIfOpen()
  } catch {
    // already surfaced via apiFetch
  } finally {
    actionLoading.value = false
    // The button that was clicked disables itself immediately (see
    // ActionButtons), which blurs it — send focus back to the chat input.
    await nextTick()
    chatWindow.value?.focusInput()
  }
}

async function handleReset() {
  if (!window.confirm('Reset the conversation, signals, and transitions? This cannot be undone.')) return
  messages.value = []
  clearApiError()
  chatStatus.value = ''
  autoTrackingEnabled.value = true
  try {
    const newState = await postReset()
    state.value = null
    handleStateChange(newState)
    await loadMessages()
    refreshSignalsIfOpen()
  } catch {
    // already surfaced via apiFetch
  }
}

function triggerModelUpload() {
  modelUploadInput.value?.click()
}

// Optimistic UI clear shared by every path that's about to leave the
// backend with a freshly reset active model — upload, switch, delete.
// Cleared immediately, before the triggering request even resolves, same
// as it always was inline here.
function clearChatUi() {
  messages.value = []
  clearApiError()
  chatStatus.value = ''
  autoTrackingEnabled.value = true
}

// The fetch-fresh-state-and-redisplay half of that same shared flow: the
// fresh state comes from a separate GET /api/state call (none of
// putModel/activateModel/deleteModel's responses carry the state payload
// itself), same as handleReset picks up the opening message via REST,
// regardless of chat transport.
async function refreshStateAndProjects() {
  const newState = await getState()
  projectsMenu.value?.refresh()
  handleStateChange(newState)
  await loadMessages()
  refreshSignalsIfOpen()
}

async function handleModelUploadChange(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // allow re-selecting the same file afterward
  if (!file) return

  const projectName = file.name.replace(/\.(zip|ya?ml)$/i, '')
  clearChatUi()
  try {
    await putProject(projectName, file)
    await refreshStateAndProjects()
  } catch {
    // already surfaced via apiFetch
  }
}

function handleModelEdit(projectName) {
  editProjectName.value = projectName
  showEditProject.value = true
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
  } catch {
    // already surfaced via apiFetch
  }
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

// Deleting the active model always falls back to "default" backend-side, so
// this behaves the same as a successful switch/upload — reload state, clear
// the chat.
async function handleModelDelete(projectName) {
  clearChatUi()
  try {
    await deleteProject(projectName)
    await refreshStateAndProjects()
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
  <SplashScreen v-if="bootStatus === 'waiting'" />
  <SplashScreen v-else-if="bootStatus === 'failed'" failed @retry="startBootSequence" />

  <div v-else-if="bootStatus === 'ready'" class="app">
    <header class="topbar">
      <StateBar :state="state" />
      <div class="topbar-actions">
        <button
          class="signals-btn"
          :class="{ 'signals-btn-on': showSignals }"
          @click="showSignals = !showSignals"
        >
          Signals
        </button>
        <ProjectsMenu
          ref="projectsMenu"
          @select="handleProjectSwitch"
          @edit="handleModelEdit"
          @upload="triggerModelUpload"
          @download="handleModelDownload"
          @delete="handleModelDelete"
        />
        <input
          ref="modelUploadInput"
          type="file"
          accept=".zip,.yml,.yaml"
          class="upload-model-input"
          @change="handleModelUploadChange"
        />
        <button class="reset-btn" @click="handleReset">Reset</button>
      </div>
    </header>

    <div class="app-body" ref="appBody" :class="{ 'app-body-resizing': resizingSignals }">
      <ChatWindow
        ref="chatWindow"
        :messages="messages"
        :loading="chatLoading"
        :status="chatStatus"
        :error-message="errorMessage"
        :error-detail="errorDetail"
        :state-chat="state?.chat ?? true"
        :history-loaded="historyLoaded"
        :audio-enabled="audioEnabled"
        :talk-available="talkAvailable"
        :mic-available="micAvailable"
        :spoken-text-enabled="spokenTextEnabled"
        @send="handleSend"
        @resend="handleResend"
        @toggle-audio="toggleAudio"
        @voice-message="handleVoiceMessage"
        @toggle-spoken-text="toggleSpokenText"
      >
        <template #actions>
          <ActionButtons
            :actions="state?.actions ?? []"
            :disabled="actionLoading"
            :auto-tracking-enabled="autoTrackingEnabled"
            @action="handleAction"
          />
        </template>
      </ChatWindow>

      <!-- Draggable split, docked mode only (hidden below the same
           >=900px breakpoint SignalsView docks at — see its own CSS). -->
      <div
        v-if="showSignals"
        class="signals-resizer"
        @pointerdown="startSignalsResize"
        @pointermove="onSignalsResizeMove"
        @pointerup="stopSignalsResize"
        @pointercancel="stopSignalsResize"
      ></div>

      <!-- Wide screens: docked inspector beside the chat, stays open across
           turns and live-refreshes (see refreshSignalsIfOpen). Narrow
           screens: SignalsView's own CSS falls back to a full-screen modal
           (the inline width below is harmless there — inset:0 wins). -->
      <SignalsView
        v-if="showSignals"
        ref="signalsView"
        :style="{ width: signalsPanelWidth + 'px' }"
        :state="state"
        :auto-tracking-enabled="autoTrackingEnabled"
        :auto-tracking-loading="autoTrackingLoading"
        @close="showSignals = false"
        @toggle-auto-tracking="toggleAutoTracking"
      />
    </div>

    <EditProjectView
      v-if="showEditProject"
      :project-name="editProjectName"
      @close="showEditProject = false"
      @saved="handleModelEditSaved"
    />
  </div>
</template>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  font-family: system-ui, -apple-system, sans-serif;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  background: #f5f5f7;
  border-bottom: 1px solid #ddd;
}

.app-body {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

/* While dragging, the pointer can slide over the chat/panel iframe-less
   content faster than it moves — force the resize cursor and kill text
   selection everywhere so the drag doesn't feel like it "catches" on
   message bubbles. */
.app-body-resizing {
  cursor: col-resize;
  user-select: none;
}

.app-body-resizing :deep(*) {
  cursor: col-resize !important;
  user-select: none !important;
}

.signals-resizer {
  display: none;
  flex: none;
  width: 6px;
  margin: 0 -3px;
  cursor: col-resize;
  touch-action: none;
  position: relative;
  z-index: 1;
}

.signals-resizer::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 2px;
  right: 2px;
  border-radius: 2px;
}

.signals-resizer:hover::after {
  background: #4a6fa5;
}

@media (min-width: 900px) {
  .signals-resizer {
    display: block;
  }
}

.topbar-actions {
  display: flex;
  gap: 0.5rem;
}

.signals-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
}

.signals-btn:hover {
  background: #4a6fa5;
  color: white;
}

.signals-btn-on {
  background: #4a6fa5;
  color: white;
}

.upload-model-input {
  display: none;
}

.reset-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
}

.reset-btn:hover {
  background: #c62828;
  color: white;
}
</style>
