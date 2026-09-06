<script setup>
// Opened from a human_takeover notification's "Open" link (see
// humanTakeoverStore.js, App.vue) — actuator.switch_to_human(user_id)
// handed this session to whoever is looking at this. A mirror of the
// normal chat: same history read (getMessages), rendered inverted since
// the operator is the one standing in for "assistant" here. Sending goes
// out as a human_reply frame — the same one WsNotifications._handle_frame
// already routes to WsHumanRelay.receive() -> HumanTalker.talk(), which
// TrackingProcessor then bubbles up to the customer through the same
// on_metadata path an AiTalker reply uses. This view never touches that
// pipeline directly, it only ever sends the one frame type that already
// does and reads what MessageBubble needs to show.
import { computed, onMounted, ref, watch } from 'vue'
import MessageBubble from './MessageBubble.vue'
import ActionButtons from './ActionButtons.vue'
import { getMessages, getOperatorState, postAction } from '../../api/chat.js'
import { chatChannel } from '../../chatChannel.js'
import { getHumanPromptForSession, removeHumanPrompt } from '../../humanPromptStore.js'

const props = defineProps({
  sessionId: { type: [Number, String], required: true }
})

defineEmits(['close'])

const messages = ref([])
const draft = ref('')
const loadFailed = ref(false)
const state = ref(null)
const actionLoading = ref(false)
let nextId = 0

function pushMessage(role, content) {
  messages.value.push({ id: ++nextId, role, content, timestamp: new Date().toISOString() })
}

onMounted(async () => {
  try {
    const [history, sessionState] = await Promise.all([
      getMessages(props.sessionId),
      getOperatorState(props.sessionId)
    ])
    messages.value = history.map((row) => ({ id: ++nextId, role: row.role, content: row.content, timestamp: row.timestamp }))
    state.value = sessionState
  } catch {
    loadFailed.value = true // already surfaced via apiFetch
  }
})

async function handleAction(actionName) {
  actionLoading.value = true
  try {
    const result = await postAction(actionName, props.sessionId)
    for (const { content } of result.reply) pushMessage('assistant', content)
    // Not result.state: that one is filtered by this session's own,
    // unrelated is_auto_tracking_enabled flag (see ChatService.
    // get_state_for_operator's own docstring) — re-fetch the operator's
    // own "every action is manual" view instead.
    state.value = await getOperatorState(props.sessionId)
  } catch {
    // already surfaced via apiFetch
  } finally {
    actionLoading.value = false
  }
}

// getHumanPromptForSession is a plain lookup (see humanPromptStore.js),
// not itself reactive to *which* prompt it is — this computed re-runs
// whenever humanPromptStore's own backing ref changes, which is what
// actually makes it reactive here. `immediate` also shows whatever prompt
// was already pending the moment this page opened, not only the next one
// — its text isn't in the history read above yet if it arrived first.
const pendingPrompt = computed(() => getHumanPromptForSession(props.sessionId))

watch(pendingPrompt, (prompt) => {
  if (prompt) pushMessage('user', prompt.text)
}, { immediate: true })

function submit() {
  const text = draft.value.trim()
  const prompt = pendingPrompt.value
  if (!text || !prompt) return
  chatChannel.send({ type: 'human_reply', prompt_id: prompt.promptId, text })
  removeHumanPrompt(prompt.promptId)
  pushMessage('assistant', text)
  draft.value = ''
}
</script>

<template>
  <div class="operator-chat">
    <div class="operator-chat-header">
      <button type="button" class="operator-chat-back" @click="$emit('close')">« Close</button>
      <span class="operator-chat-title">Answering as human — session {{ sessionId }}</span>
    </div>

    <div class="operator-chat-body">
      <p v-if="loadFailed" class="operator-chat-error">Couldn't load this session's history.</p>
      <MessageBubble v-for="msg in messages" :key="msg.id" :message="msg" invert show-timestamp />
    </div>

    <ActionButtons
      :actions="state?.manual_actions || []"
      :disabled="actionLoading"
      @action="handleAction"
    />

    <form class="operator-chat-footer" @submit.prevent="submit">
      <textarea
        v-model="draft"
        class="operator-chat-input"
        rows="2"
        :placeholder="pendingPrompt ? 'Type the reply…' : 'Waiting for the next message…'"
        :disabled="!pendingPrompt"
        @keydown.enter.exact.prevent="submit"
      ></textarea>
      <button type="submit" class="operator-chat-send" :disabled="!pendingPrompt || !draft.trim()">Send</button>
    </form>
  </div>
</template>

<style scoped>
.operator-chat {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  flex-direction: column;
  background: white;
}

.operator-chat-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
  border-bottom: 1px solid #eee;
}

.operator-chat-back {
  border: none;
  background: none;
  font-size: 0.85rem;
  cursor: pointer;
  color: #2b6cb0;
}

.operator-chat-title {
  font-weight: 600;
  font-size: 0.9rem;
  color: #333;
}

.operator-chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.operator-chat-error {
  color: #b00;
  font-size: 0.85rem;
}

.operator-chat-footer {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-top: 1px solid #eee;
}

.operator-chat-input {
  flex: 1;
  resize: none;
  font: inherit;
  padding: 0.5rem;
  border: 1px solid #ddd;
  border-radius: 6px;
}

.operator-chat-send {
  border: none;
  border-radius: 6px;
  background: #2b6cb0;
  color: white;
  padding: 0 1rem;
  cursor: pointer;
}

.operator-chat-send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
