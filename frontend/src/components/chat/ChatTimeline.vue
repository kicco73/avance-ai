<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import MessageBubble from './MessageBubble.vue'
import { BottomAnchor } from './bottomAnchor.js'
import { messageHasAnnotatedSignals } from '../../testTimeline.js'

const props = defineProps({
  timeline: { type: Array, required: true },
  signalsLog: { type: Array, default: () => [] },
  selected: { type: Object, default: null },
  spokenTextEnabled: { type: Boolean, default: false },
  imported: { type: Boolean, default: false },
  resolveStateLabel: { type: Function, default: null },
  resolveActionLabel: { type: Function, default: null },
  autoScroll: { type: Boolean, default: true },
  reactions: { type: Array, default: () => [] }
})

function stateLabel(stateKey) {
  return props.resolveStateLabel ? props.resolveStateLabel(stateKey) : stateKey
}

function actionLabel(transition) {
  return props.resolveActionLabel ? props.resolveActionLabel(transition.old_state, transition.action) : null
}

const emit = defineEmits(['select-message', 'select-transition', 'react'])

const rootEl = ref(null)
const contentEl = ref(null)
const anchor = new BottomAnchor()

watch([rootEl, contentEl], ([scroller, content]) => {
  anchor.detach()
  if (scroller && content && props.autoScroll) anchor.attach(scroller, content)
})

onBeforeUnmount(() => anchor.detach())

function toBubbleMessage(m) {
  return { ...m, audioText: m.audio_text }
}

function isMessageSelected(message) {
  return props.selected?.kind === 'message' && props.selected.message.id === message.id
}

function isTransitionSelected(transition) {
  return props.selected?.kind === 'transition' && props.selected.transition.id === transition.id
}

function isSelfLoop(transition) {
  return transition.old_state === transition.new_state
}
</script>

<template>
  <div class="chat-timeline" ref="rootEl" @scroll="anchor.onScroll()">
    <div class="chat-timeline-content" ref="contentEl">
    <template
      v-for="entry in timeline"
      :key="entry.kind + '-' + (entry.kind === 'message' ? (entry.message.key ?? entry.message.id) : entry.transition.id)"
    >
      <div
        v-if="entry.kind === 'message'"
        class="timeline-row timeline-message-row"
        :class="[
          entry.message.role === 'user' ? 'timeline-message-row-user' : 'timeline-message-row-assistant',
          { 'timeline-row-selected': isMessageSelected(entry.message) }
        ]"
        @click="emit('select-message', entry.message)"
      >
        <span class="timeline-message-actions" @click.stop>
          <slot name="message-actions" :message="entry.message" />
        </span>
        <MessageBubble
          :message="toBubbleMessage(entry.message)"
          show-timestamp
          :spoken-text-enabled="spokenTextEnabled"
          :signals-annotated="messageHasAnnotatedSignals(entry.message, signalsLog)"
          :imported="imported"
          :reactions="reactions"
          @react="emit('react', entry.message.id, $event)"
        />
      </div>

      <div
        v-else
        class="timeline-row timeline-transition-row"
        :class="[
          { 'timeline-row-selected': isTransitionSelected(entry.transition), 'timeline-transition-row-self-loop': isSelfLoop(entry.transition) },
          entry.annotationStatus ? `timeline-transition-row-${entry.annotationStatus}` : ''
        ]"
        @click="emit('select-transition', entry.transition)"
      >
        <span
          class="timeline-transition-arrow"
          :title="isSelfLoop(entry.transition) ? 'No actual state change here' : ''"
        >{{ isSelfLoop(entry.transition) ? '↻' : '→' }}</span>
        <span
          v-if="isSelfLoop(entry.transition) && actionLabel(entry.transition)"
          class="timeline-transition-action-badge"
        >{{ actionLabel(entry.transition) }}</span>
        <span class="timeline-transition-badge">{{ stateLabel(entry.transition.new_state) }}</span>
        <span
          v-if="entry.annotationStatus === 'correct'"
          class="timeline-transition-annotation-icon timeline-transition-annotation-icon-correct"
          title="Matches the expert-annotated expected state"
        >✓</span>
        <span
          v-else-if="entry.annotationStatus === 'incorrect'"
          class="timeline-transition-annotation-icon timeline-transition-annotation-icon-incorrect"
          title="Differs from the expert-annotated expected state"
        >✕</span>
        <span
          v-else-if="entry.annotationStatus === 'labelled'"
          class="timeline-transition-annotation-icon timeline-transition-annotation-icon-labelled"
          title="Expert-labelled — no avance-computed state to compare against on an imported session"
        >✓</span>
      </div>
    </template>
    </div>
  </div>
</template>

<style scoped>
.chat-timeline {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.chat-timeline-content {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.timeline-row {
  display: flex;
  align-items: center;
  padding: 0.5rem 1rem;
  cursor: pointer;
}

.timeline-message-actions {
  display: contents;
}

.timeline-row:hover {
  background: #f7f9fc;
}

.timeline-row-selected {
  background: #e3ebf7;
}

.timeline-message-row {
  justify-content: flex-start;
}

.timeline-message-row-user {
  justify-content: flex-end;
}

.timeline-message-row-assistant {
  justify-content: flex-start;
}

.timeline-transition-row {
  justify-content: center;
  align-items: center;
  gap: 0.5rem;
  background: #fbf3e6;
}

.timeline-transition-row:hover {
  background: #f6e9d2;
}

.timeline-transition-row.timeline-row-selected {
  background: #f0dcb0;
}

.timeline-transition-row-self-loop {
  opacity: 0.5;
}

.timeline-transition-row-correct {
  background: #e8f5e9;
}

.timeline-transition-row-correct:hover {
  background: #dcefdd;
}

.timeline-transition-row-correct.timeline-row-selected {
  background: #c8e6c9;
}

.timeline-transition-row-incorrect {
  background: #fdecea;
}

.timeline-transition-row-incorrect:hover {
  background: #fbdedb;
}

.timeline-transition-row-incorrect.timeline-row-selected {
  background: #f5c6c2;
}

.timeline-transition-row-labelled {
  background: #e8f5e9;
}

.timeline-transition-row-labelled:hover {
  background: #dcefdd;
}

.timeline-transition-row-labelled.timeline-row-selected {
  background: #c8e6c9;
}

.timeline-transition-arrow {
  color: #8a6d3b;
  font-weight: 600;
}

.timeline-transition-badge {
  display: inline-block;
  padding: 0.15rem 0.7rem;
  border-radius: 999px;
  background: #4a6fa5;
  color: white;
  font-size: 0.78rem;
  font-weight: 600;
}

.timeline-transition-action-badge {
  display: inline-block;
  padding: 0.15rem 0.7rem;
  border-radius: 999px;
  background: #8a6d3b;
  color: white;
  font-size: 0.78rem;
  font-weight: 600;
}

.timeline-transition-annotation-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.2rem;
  height: 1.2rem;
  border-radius: 50%;
  font-size: 0.72rem;
  font-weight: 700;
  color: white;
}

.timeline-transition-annotation-icon-correct {
  background: #2e7d32;
}

.timeline-transition-annotation-icon-incorrect {
  background: #c62828;
}

.timeline-transition-annotation-icon-labelled {
  background: #2e7d32;
}
</style>
