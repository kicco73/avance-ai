<script setup>
// Surface for HumanTalker's manual-testing seam (see talker.human_talker,
// chat/ws_human_relay.py, humanPromptBus.js) — mounted once at the app
// root alongside ToastContainer.vue, whose visual language this mirrors,
// but each card here holds its own reply box: answering doesn't require
// navigating to the session first, since the reply travels back over the
// socket by prompt_id regardless of what page this tab is showing.
import { ref } from 'vue'
import { humanPrompts } from '../humanPromptStore.js'
import { sendHumanReply, dismissHumanPrompt } from '../humanPromptBus.js'

const drafts = ref({})

function submit(promptId) {
  const text = (drafts.value[promptId] || '').trim()
  if (!text) return
  sendHumanReply(promptId, text)
  delete drafts.value[promptId]
}

function dismiss(promptId) {
  dismissHumanPrompt(promptId)
  delete drafts.value[promptId]
}

function sessionLabel(prompt) {
  const parts = [`session ${prompt.sessionId}`]
  if (prompt.sessionType) parts.push(prompt.sessionType)
  if (prompt.projectId) parts.push(prompt.projectId)
  return parts.join(' · ')
}
</script>

<template>
  <div class="human-prompt-container">
    <TransitionGroup name="human-prompt">
      <div v-for="prompt in humanPrompts" :key="prompt.promptId" class="human-prompt-card">
        <div class="human-prompt-header">
          <span class="human-prompt-title">Answer as human — {{ sessionLabel(prompt) }}</span>
          <button class="human-prompt-close" title="Dismiss" @click="dismiss(prompt.promptId)">×</button>
        </div>
        <div class="human-prompt-body">{{ prompt.text }}</div>
        <form class="human-prompt-form" @submit.prevent="submit(prompt.promptId)">
          <textarea
            v-model="drafts[prompt.promptId]"
            class="human-prompt-input"
            rows="2"
            placeholder="Type the reply…"
            @keydown.enter.exact.prevent="submit(prompt.promptId)"
          ></textarea>
          <button type="submit" class="human-prompt-submit">Send</button>
        </form>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.human-prompt-container {
  position: fixed;
  top: 1rem;
  right: 1rem;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  width: 320px;
  max-width: calc(100vw - 2rem);
  pointer-events: none;
}

.human-prompt-card {
  pointer-events: auto;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
  overflow: hidden;
}

.human-prompt-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.5rem 0.6rem;
  background: #f5f5f7;
  border-bottom: 1px solid #eee;
}

.human-prompt-title {
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.human-prompt-close {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #666;
  cursor: pointer;
  font-size: 1rem;
}

.human-prompt-close:hover {
  background: #e5e5e5;
}

.human-prompt-body {
  padding: 0.5rem 0.6rem;
  font-size: 0.82rem;
  line-height: 1.4;
  color: #444;
  white-space: pre-wrap;
}

.human-prompt-form {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0 0.6rem 0.6rem;
}

.human-prompt-input {
  resize: vertical;
  font: inherit;
  font-size: 0.82rem;
  padding: 0.4rem;
  border: 1px solid #ddd;
  border-radius: 6px;
}

.human-prompt-submit {
  align-self: flex-end;
  border: none;
  border-radius: 6px;
  background: #2b6cb0;
  color: white;
  font-size: 0.82rem;
  padding: 0.3rem 0.8rem;
  cursor: pointer;
}

.human-prompt-submit:hover {
  background: #245a94;
}

.human-prompt-enter-active, .human-prompt-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.human-prompt-enter-from {
  opacity: 0;
  transform: translateX(20px);
}

.human-prompt-leave-to {
  opacity: 0;
}
</style>
