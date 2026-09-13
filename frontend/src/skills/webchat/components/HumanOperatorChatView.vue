<script setup>
// Opened from a session.taken_over notification's "Open" link (see
// humanTakeoverStore.js, App.vue) — chat.switch_to_human(user_id)
// handed this session to whoever is looking at this. A mirror of the
// normal chat: it enters the same conversation on the bus and is told
// what was said and what it offers, rendered inverted since the operator
// is the one standing in for "assistant" here (MessageBubble's own
// invert prop). Sending and typing both go out keyed by session_id,
// never a prompt_id the operator's own tab may never have seen (a fresh
// human_prompt push is one-shot — a tab that opens after it already fired
// would otherwise never know what to reply to) — see system/bus_channel.
// py's own _current_prompt_for_session.
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import MessageBubble from '../../../components/chat/MessageBubble.vue'
import ActionButtons from '../../../components/chat/ActionButtons.vue'
import { busChannel } from '../../../busChannel.js'
import { getHumanPromptForSession, removeHumanPrompt } from '../../../humanPromptStore.js'
import { listenForHumanPrompts } from '../../../humanPromptBus.js'

const props = defineProps({
  sessionId: { type: [Number, String], required: true }
})

defineEmits(['close'])

const messages = ref([])
const draft = ref('')
const loadFailed = ref(false)
const buttons = ref([])
const actionLoading = ref(false)
let nextId = 0
let typingSent = false

function pushMessage(role, content) {
  messages.value.push({ id: ++nextId, role, content, timestamp: new Date().toISOString() })
}

let stopListeningForPrompts = null
const unsubscribes = []

function mine(frame) {
  return String(frame.session_id) === String(props.sessionId)
}

onMounted(() => {
  stopListeningForPrompts = listenForHumanPrompts()
  unsubscribes.push(busChannel.subscribe('session.messages', (frame) => {
    if (!mine(frame)) return
    messages.value = (frame.messages || []).map(
      (row) => ({ id: ++nextId, role: row.role, content: row.content, timestamp: row.timestamp })
    )
  }))
  unsubscribes.push(busChannel.subscribe('state.buttons', (frame) => {
    if (!mine(frame)) return
    buttons.value = frame.actions || []
  }))
  loadFailed.value = !busChannel.send({ type: 'session.enter', session_id: props.sessionId })
})

onUnmounted(() => {
  stopListeningForPrompts?.()
  stopListeningForPrompts = null
  busChannel.send({ type: 'session.exit', session_id: props.sessionId })
  for (const unsubscribe of unsubscribes.splice(0)) unsubscribe()
})

// A choice taken travels the way every other one does — `input.button`
// on the socket (see backend docs/BUS.md), and what the new state offers
// comes back on `state.buttons` like it does for anybody else.
function handleAction(actionName) {
  actionLoading.value = true
  const taken = busChannel.send({ type: 'input.button', session_id: props.sessionId, id: actionName })
  actionLoading.value = false
  if (taken) buttons.value = []
}

// getHumanPromptForSession is a plain lookup (see humanPromptStore.js),
// not itself reactive to *which* prompt it is — this computed re-runs
// whenever humanPromptStore's own backing ref changes, which is what
// actually makes it reactive here. `immediate` also shows whatever prompt
// was already pending the moment this page opened, not only the next one
// — its text isn't in the history read above yet if it arrived first.
// Display only: sending never depends on this having resolved (see
// submit() below) — a missed push must never leave the operator unable
// to write.
const pendingPrompt = computed(() => getHumanPromptForSession(props.sessionId))

watch(pendingPrompt, (prompt) => {
  if (prompt) {
    pushMessage('user', prompt.text)
    typingSent = false
  }
}, { immediate: true })

function onDraftInput() {
  if (typingSent || !draft.value.trim()) return
  typingSent = true
  busChannel.send({ type: 'human_typing', session_id: props.sessionId })
}

function submit() {
  const text = draft.value.trim()
  if (!text) return
  busChannel.send({ type: 'human_reply', session_id: props.sessionId, text })
  if (pendingPrompt.value) removeHumanPrompt(pendingPrompt.value.promptId)
  pushMessage('assistant', text)
  draft.value = ''
  typingSent = false
}
</script>

<template>
  <div class="chat-window-outer operator-chat">
    <div class="chat-window-shell">
      <div class="chat-window">
        <div class="chat-header">
          <button type="button" class="operator-back" @click="$emit('close')">« Close</button>
          <span class="operator-title">Answering as human — session {{ sessionId }}</span>
        </div>

        <div class="messages chat-body">
          <p v-if="loadFailed" class="chat-ended-notice">Couldn't load this session's history.</p>
          <MessageBubble v-for="msg in messages" :key="msg.id" :message="msg" invert show-timestamp />
        </div>

        <div class="chat-footer">
          <ActionButtons
            :actions="buttons"
            :disabled="actionLoading"
            @action="handleAction"
          />
          <form class="operator-input-row" @submit.prevent="submit">
            <textarea
              v-model="draft"
              class="operator-input"
              rows="1"
              placeholder="Type a message…"
              @input="onDraftInput"
              @keydown.enter.exact.prevent="submit"
            ></textarea>
            <button type="submit" class="operator-send" :disabled="!draft.trim()">Send</button>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Same shell classes ChatView.vue uses — .chat-header/.chat-body/
   .chat-footer are deliberately bare style hooks (see that file's own
   comment) so a project's own skin (a single, unscoped app-wide <style>,
   see chatSkin.js) paints this page the same way it paints the real
   chat, without this component reaching into ChatView.vue's internals
   or its customer-only session-lifecycle UI (new/close session,
   autotracking/actuators toggles) at all. */
.chat-window-outer {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  background: white;
}

.chat-window-shell {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  min-width: 0;
}

.chat-window {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
}

.chat-header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
  padding-top: calc(var(--safe-area-top, 0px) + 0.6rem);
  border-bottom: 1px solid #eee;
}

.operator-back {
  border: none;
  background: none;
  font-size: 0.85rem;
  cursor: pointer;
  color: #2b6cb0;
}

.operator-title {
  font-weight: 600;
  font-size: 0.9rem;
  color: #333;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  overscroll-behavior-y: contain;
}

.chat-ended-notice {
  color: #b00;
  font-size: 0.85rem;
  margin: 0;
}

.chat-footer {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  padding-bottom: var(--safe-area-bottom, 0px);
}

.operator-input-row {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
}

.operator-input {
  flex: 1;
  resize: none;
  font: inherit;
  padding: 0.5rem;
  border: 1px solid #ddd;
  border-radius: 6px;
}

.operator-send {
  border: none;
  border-radius: 6px;
  background: #2b6cb0;
  color: white;
  padding: 0 1rem;
  cursor: pointer;
}

.operator-send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
