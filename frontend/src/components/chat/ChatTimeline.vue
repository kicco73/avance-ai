<script setup>
// Chronological, clickable message+transition list — shared by
// BenchmarkProjectView.vue (reviewing a fixed past session) and
// EditProjectView.vue (reviewing the live session as it happens). Both
// feed it the same shapes (see benchmarkTimeline.js's buildTimeline: each
// entry is { kind: 'message', message } or { kind: 'transition',
// transition, annotationStatus }), so this component has no notion of
// "benchmark" vs "live" mode of its own — annotationStatus/signalsAnnotated
// simply read as null/false wherever the caller's own signalsLog has
// nothing annotated (a live session's log never does, unless it was
// annotated from Label sessions too).
import { nextTick, ref, watch } from 'vue'
import MessageBubble from './MessageBubble.vue'
import { messageHasAnnotatedSignals } from '../../benchmarkTimeline.js'

const props = defineProps({
  timeline: { type: Array, required: true },
  signalsLog: { type: Array, default: () => [] },
  selected: { type: Object, default: null },
  // See MessageBubble.vue's own prop — this component has no opinion of
  // its own on whether spoken text should show, just forwards whatever
  // the caller (EditProjectView.vue's chat, via chatStore.js's shared
  // toggle) decides. Defaults to false so BenchmarkProjectView.vue,
  // which never passes this at all, is unaffected.
  spokenTextEnabled: { type: Boolean, default: false }
})

const emit = defineEmits(['select-message', 'select-transition'])

const rootEl = ref(null)

// This root is the actual scroll region (see .chat-timeline's own
// overflow-y below) — not whatever wraps it. A parent that also happens
// to be scrollable itself (see ChatWindow.vue's own .messages, when this
// is slotted into it) never actually overflows once this absorbs its own
// content instead, so a parent-driven auto-scroll silently no-ops there;
// this has to own the behavior itself. buildTimeline() (see
// benchmarkTimeline.js) always returns a fresh array, never mutates one
// in place, so a shallow watch on the prop reference is enough to catch
// every change.
function scrollToBottom() {
  nextTick(() => {
    if (rootEl.value) rootEl.value.scrollTop = rootEl.value.scrollHeight
  })
}

watch(() => props.timeline, scrollToBottom)

function toBubbleMessage(m) {
  return { role: m.role, content: m.content, audioText: m.audio_text, timestamp: m.timestamp }
}

function isMessageSelected(message) {
  return props.selected?.kind === 'message' && props.selected.message.id === message.id
}

function isTransitionSelected(transition) {
  return props.selected?.kind === 'transition' && props.selected.transition.id === transition.id
}

// A fired action that left the state unchanged (see benchmarkTimeline.js's
// buildTimeline own includeSelfLoops — only EditProjectView.vue's live
// chat ever includes one of these unannotated) still happened and is
// worth showing, just visually de-emphasized (see .timeline-transition-
// row-self-loop below) since nothing about the conversation's own state
// actually moved.
function isSelfLoop(transition) {
  return transition.old_state === transition.new_state
}
</script>

<template>
  <div class="chat-timeline" ref="rootEl">
    <template
      v-for="entry in timeline"
      :key="entry.kind + '-' + (entry.kind === 'message' ? entry.message.id : entry.transition.id)"
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
        <span class="timeline-transition-badge">{{ entry.transition.new_state }}</span>
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
      </div>
    </template>
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

.timeline-row {
  display: flex;
  align-items: center;
  padding: 0.5rem 1rem;
  cursor: pointer;
}

/* display:contents so an empty slot (every use besides EditProjectView's
   restart-from-here button) contributes no box/spacing of its own —
   whatever the slot does render is responsible for its own margin. */
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

/* A fired self-loop (see isSelfLoop) — de-emphasized rather than hidden,
   since the conversation's own state genuinely didn't move here. */
.timeline-transition-row-self-loop {
  opacity: 0.5;
}

/* Whether the transition's own expert-annotated expected_state agrees
   with what actually happened (see transitionAnnotationStatus) — lets a
   reviewer spot a mismatch across the whole timeline at a glance, not
   just by opening the Inspector on each one. */
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
</style>
