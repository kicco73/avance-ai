<script setup>
import { nextTick, ref, watch } from 'vue'
import { putSessionTitle, putSessionComment } from '../../api.js'
import { vAutosize } from './textareaAutosize.js'
import { handleEnterNext } from './enterToNextField.js'

const props = defineProps({
  session: { type: Object, required: true }
})

const emit = defineEmits(['updated'])

const expanded = ref(false)
const editTitle = ref('')
const editComment = ref('')
const titleInputRef = ref(null)

watch(() => props.session.id, () => { expanded.value = false })
watch(() => props.session, (session) => {
  editTitle.value = session?.title ?? ''
  editComment.value = session?.comment ?? ''
}, { immediate: true })

function formatSessionTimestamp(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

async function toggle() {
  expanded.value = !expanded.value
  if (expanded.value) {
    await nextTick()
    titleInputRef.value?.focus()
  }
}

async function onUpdateTitle() {
  if (editTitle.value === (props.session.title ?? '')) return
  await putSessionTitle(props.session.id, editTitle.value)
  emit('updated')
}

async function onUpdateComment() {
  if (editComment.value === (props.session.comment ?? '')) return
  await putSessionComment(props.session.id, editComment.value)
  emit('updated')
}
</script>

<template>
  <div class="session-detail-card" title="Click to open" @click="toggle">
    <Transition name="crossfade" mode="out-in">
      <div v-if="expanded" key="edit" class="session-detail-form">
        <div class="session-detail-header">
          <span class="session-detail-badge">Session</span>
          <input
            ref="titleInputRef"
            v-model="editTitle"
            class="session-detail-label-input"
            placeholder="Untitled session"
            @click.stop
            @blur="onUpdateTitle"
            @keydown.enter.prevent="handleEnterNext"
          />
        </div>
        <label class="session-detail-form-label">Started</label>
        <p class="session-detail-value">{{ formatSessionTimestamp(session.datetime_start) }}</p>
        <label class="session-detail-form-label">Ended</label>
        <p class="session-detail-value">{{ formatSessionTimestamp(session.datetime_end) }}</p>
        <label class="session-detail-form-label">Comment</label>
        <textarea
          v-model="editComment"
          v-autosize
          class="session-detail-textarea"
          rows="3"
          placeholder="No comment yet."
          @click.stop
          @blur="onUpdateComment"
        ></textarea>
      </div>
      <div v-else key="readonly" class="session-detail-readonly">
        <div class="session-detail-header">
          <span class="session-detail-badge">Session</span>
          <span class="session-detail-name">{{ session.title || session.end_state || 'Untitled session' }}</span>
        </div>
        <span v-if="session.comment" class="session-detail-comment-preview">{{ session.comment }}</span>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.session-detail-card {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  margin-bottom: 0.75rem;
  padding: 0.6rem 0.75rem;
  border-radius: 8px;
  border: 1px solid #eee;
  background: #fafafa;
  cursor: pointer;
}

.session-detail-card:hover {
  border-color: #c9d6e8;
  background: #f0f4fa;
}

.session-detail-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.session-detail-badge {
  flex-shrink: 0;
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  color: white;
  background: #455a64;
}

.session-detail-name {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
}

.session-detail-label-input {
  flex: 1;
  min-width: 0;
  font-weight: 600;
  font-size: 0.85rem;
  color: #333;
  border: 1px solid transparent;
  border-radius: 4px;
  padding: 0.1rem 0.3rem;
  background: transparent;
}

.session-detail-label-input:hover,
.session-detail-label-input:focus {
  border-color: #ccc;
  background: white;
}

.session-detail-form-label {
  display: block;
  margin: 0.6rem 0 0.15rem;
  font-size: 0.68rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: #777;
}

.session-detail-value {
  margin: 0.15rem 0 0;
  font-size: 0.85rem;
  color: #333;
  word-break: break-word;
}

.session-detail-textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  font: inherit;
  font-size: 0.78rem;
  line-height: 1.54;
  padding: 0.35rem 0.5rem;
  border-radius: 6px;
  border: 1px solid #ccc;
}

.session-detail-comment-preview {
  font-size: 0.78rem;
  color: #666;
  line-height: 1.4;
}

.crossfade-enter-active,
.crossfade-leave-active {
  transition: opacity 0.15s ease;
}

.crossfade-enter-from,
.crossfade-leave-to {
  opacity: 0;
}
</style>
