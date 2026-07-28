import { nextTick, ref } from 'vue'
import {
  getMessages,
  postAction,
  getAutoTracking,
  postAutoTracking,
  messageAudioUrl,
  postListenTranscribe,
  postReset
} from './api.js'
import { sendMessage as sendChatMessage } from './chatClient.js'
import { playMessageChime, playMessageAudio } from './audio.js'
import { celebrate } from './confetti.js'
import { clearApiError } from './errorStore.js'

// The single shared conversation — every chat view (the main app's, see
// App.vue, and the "Edit project" view's embedded one, see
// EditProjectView.vue) is just a render of this same state: ChatWindow.vue
// reads everything here directly instead of taking it as props, so a
// message/action/reset from either view is instantly reflected in both.
export const state = ref(null)
export const messages = ref([])
export const historyLoaded = ref(false)
export const chatLoading = ref(false)
export const chatStatus = ref('')
export const actionLoading = ref(false)
export const autoTrackingEnabled = ref(true)
export const autoTrackingLoading = ref(false)
// The chat input box's own draft text — shared like everything else above,
// so typing in one view's input shows up in the other's too.
export const draft = ref('')

// Pure frontend state — the backend generates audio on demand whenever
// this is on, no persisted toggle server-side (see maybeAutoPlayAudio).
export const audioEnabled = ref(false)
// Whether the server actually has talk-service/listen-service configured
// (see backend/.config.yml's `enabled`) — set once via setCapabilities()
// from App.vue's boot ping and never touched again: unlike `state`, this
// isn't per-turn data, so it must not be overwritten by a later chat/
// action response that doesn't carry these fields at all.
export const talkAvailable = ref(true)
export const micAvailable = ref(true)
// When on, assistant bubbles show audio_text (the short narrated phrase,
// see backend's [audio] tag) instead of the full reply — purely a display
// switch, no playback triggered (see toggleSpokenText).
export const spokenTextEnabled = ref(false)

// Bumped at the end of every turn (chat/action/reset), even one that
// didn't change `state` — a signal value can shift without a transition.
// External UI that needs to react to "something happened" (e.g.
// EditProjectView.vue's Inspector, keeping its graph/signals live) watches
// this instead of the store reaching into that UI directly.
export const turnCount = ref(0)

let nextMessageId = 0

function bumpTurn() {
  turnCount.value++
}

export function setCapabilities({ talkAvailable: talk, micAvailable: mic }) {
  talkAvailable.value = talk
  micAvailable.value = mic
}

export function handleStateChange(newState) {
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
export async function loadMessages() {
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
    // Gates ChatWindow's bump-in animation (see its historyLoaded usage):
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

export async function loadAutoTracking() {
  try {
    const res = await getAutoTracking()
    autoTrackingEnabled.value = res.enabled
  } catch {
    // already surfaced via apiFetch
  }
}

export async function toggleAutoTracking() {
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

export function toggleAudio() {
  audioEnabled.value = !audioEnabled.value
  if (audioEnabled.value) {
    const lastAssistant = [...messages.value].reverse().find((m) => m.role === 'assistant' && m.messageId != null)
    if (lastAssistant) playMessageAudio(messageAudioUrl(lastAssistant.messageId))
  }
}

// Purely a display switch — flipping it doesn't touch playback, only
// which text ChatWindow renders for assistant bubbles. Reactivity alone
// re-renders every bubble in every open view.
export function toggleSpokenText() {
  spokenTextEnabled.value = !spokenTextEnabled.value
}

// Fires the automatic narration for the last message a turn produced —
// same call regardless of which transport delivered it and a no-op if the
// toggle is off or the message has no id to look up.
function maybeAutoPlayAudio(messageId) {
  if (!audioEnabled.value || messageId == null) return
  playMessageAudio(messageAudioUrl(messageId))
}

// Looks the message back up by id through the reactive `messages` array
// (rather than mutating whatever reference the caller passed in) so the
// assignment goes through Vue's reactive proxy and updates the UI
// immediately in every open view.
function setMessageFailed(id, failed) {
  const target = messages.value.find((m) => m.id === id)
  if (target) target.failed = failed
}

async function submitMessage(message) {
  clearApiError()
  setMessageFailed(message.id, false)
  chatLoading.value = true
  try {
    // sendChatMessage() (chatClient.js) tries the websocket first and falls
    // back to REST transparently — this code never knows which one ran.
    const result = await sendChatMessage(message.content, {
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
    bumpTurn()
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

export async function handleSend(text) {
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
export async function handleVoiceMessage(audioBlob) {
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

export async function handleResend(index) {
  if (chatLoading.value) return
  const message = messages.value[index]
  if (!message || message.role !== 'user') return
  await submitMessage(message)
}

export async function handleAction(actionName) {
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
    bumpTurn()
  } catch {
    // already surfaced via apiFetch
  } finally {
    actionLoading.value = false
  }
}

// Optimistic UI clear shared by every path that's about to leave the
// backend with a freshly reset active project — a manual reset (below) and
// every project switch/upload/delete driven by App.vue.
export function clearChatUi() {
  messages.value = []
  clearApiError()
  chatStatus.value = ''
  autoTrackingEnabled.value = true
}

export async function handleReset() {
  if (!window.confirm('Reset the conversation, signals, and transitions? This cannot be undone.')) return
  clearChatUi()
  try {
    const newState = await postReset()
    state.value = null
    handleStateChange(newState)
    await loadMessages()
    bumpTurn()
  } catch {
    // already surfaced via apiFetch
  }
}
