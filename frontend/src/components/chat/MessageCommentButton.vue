<script setup>
// Per-message expert comment: a free-text note that can be left on any
// chat line. Unlike expected_state/expected_values, there is no
// evaluation-point gating — every message gets the icon, always enabled.
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  // null/'' = no comment yet (outline icon); a non-empty string = has
  // one (filled icon) and pre-fills the popover's own textarea on open.
  comment: { type: String, default: null }
})

// 'save' carries the trimmed text, or null to clear — the parent owns
// the actual API call, so this component has no idea one even exists.
const emit = defineEmits(['save'])

const open = ref(false)
const draft = ref('')
const buttonRef = ref(null)
const popoverRef = ref(null)
const textareaRef = ref(null)
const style = ref({})

// Snapshots the trigger button's rect once, on open, for fixed
// positioning of the popover.
function position() {
  const el = buttonRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  style.value = { top: `${rect.bottom + 6}px`, left: `${rect.left}px` }
}

function openPopover() {
  draft.value = props.comment || ''
  position()
  open.value = true
  nextTick(() => textareaRef.value?.focus())
}

// Dismissible without saving — Escape, clicking away, or the Cancel
// button all land here; only save() below ever emits anything.
function closePopover() {
  open.value = false
}

function toggle() {
  if (open.value) closePopover()
  else openPopover()
}

function save() {
  const trimmed = draft.value.trim()
  emit('save', trimmed || null)
  closePopover()
}

function onDocumentMousedown(event) {
  if (popoverRef.value?.contains(event.target) || buttonRef.value?.contains(event.target)) return
  closePopover()
}

// Only listens while the popover is open, to avoid a document-level
// listener per message bubble.
watch(open, (isOpen) => {
  if (isOpen) document.addEventListener('mousedown', onDocumentMousedown)
  else document.removeEventListener('mousedown', onDocumentMousedown)
})

onBeforeUnmount(() => document.removeEventListener('mousedown', onDocumentMousedown))
</script>

<template>
  <span class="comment-btn-wrap">
    <button
      ref="buttonRef"
      type="button"
      class="comment-btn"
      :class="{ 'comment-btn-active': !!comment }"
      :title="comment ? 'Edit comment' : 'Add a comment'"
      @click.stop="toggle"
    >
      <svg viewBox="0 0 24 24" width="14" height="14" :fill="comment ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2">
        <path
          d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        ref="popoverRef"
        class="comment-popover"
        :style="style"
        @click.stop
        @keydown.esc="closePopover"
      >
        <textarea
          ref="textareaRef"
          v-model="draft"
          class="comment-popover-textarea"
          rows="3"
          placeholder="Leave a comment for whoever reviews this next…"
        ></textarea>
        <div class="comment-popover-actions">
          <button type="button" class="comment-popover-cancel" @click="closePopover">Cancel</button>
          <button type="button" class="comment-popover-save" @click="save">Save</button>
        </div>
      </div>
    </Teleport>
  </span>
</template>

<style scoped>
.comment-btn-wrap {
  display: contents;
}

.comment-btn {
  flex: none;
  align-self: center;
  margin-right: 0.4rem;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 50%;
  border: 1px solid #999;
  background: white;
  color: #777;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.comment-btn:hover {
  background: #f0f0f0;
  color: #333;
}

/* Has a saved comment already; blue rather than amber/green so it never
   reads as a state/signal verdict. */
.comment-btn-active {
  border-color: #4a6fa5;
  color: #4a6fa5;
  background: #eaf1fb;
}

.comment-btn-active:hover {
  background: #dceafd;
}

/* Teleported to <body>, position: fixed — see position() above. */
.comment-popover {
  position: fixed;
  z-index: 1000;
  width: 260px;
  max-width: calc(100vw - 2rem);
  padding: 0.6rem;
  border-radius: 8px;
  background: white;
  border: 1px solid #ccc;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.comment-popover-textarea {
  width: 100%;
  resize: vertical;
  font: inherit;
  font-size: 0.82rem;
  padding: 0.4rem;
  border: 1px solid #ccc;
  border-radius: 6px;
  box-sizing: border-box;
}

.comment-popover-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.4rem;
}

.comment-popover-cancel {
  padding: 0.3rem 0.7rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  background: white;
  cursor: pointer;
  font-size: 0.8rem;
}

.comment-popover-cancel:hover {
  background: #f0f0f0;
}

.comment-popover-save {
  padding: 0.3rem 0.7rem;
  border-radius: 6px;
  border: 1px solid #2e7d32;
  background: #2e7d32;
  color: white;
  cursor: pointer;
  font-size: 0.8rem;
}

.comment-popover-save:hover {
  background: #256428;
}
</style>
