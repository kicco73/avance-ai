<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { putSessionTitle, putSessionComment } from '../../api.js'
import { vAutosize } from './textareaAutosize.js'
import { handleEnterNext } from './enterToNextField.js'
import CardMenu from './CardMenu.vue'
import { useTokensBar } from '../../composables/useTokensBar.js'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'

const props = defineProps({
  session: { type: Object, required: true },
  editable: { type: Boolean, default: true },
  deletable: { type: Boolean, default: false },
  sessionInputTokens: { type: Number, default: null },
  inputTokenBudgetPerSession: { type: Number, default: null }
})

const emit = defineEmits(['updated', 'delete'])

const expanded = ref(false)
const editTitle = ref('')
const editComment = ref('')
const titleInputRef = ref(null)

watch(() => props.session.id, () => { expanded.value = false })
watch(() => props.session, (session) => {
  editTitle.value = session?.title ?? ''
  editComment.value = session?.comment ?? ''
}, { immediate: true })

const isImported = computed(() => props.session.type === 'imported')
const hasTimestamps = computed(() => props.session.datetime_start != null || props.session.datetime_end != null)

function formatSessionTimestamp(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

async function toggle() {
  if (!props.editable) return
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

const { width: tokensBarWidth, level: tokensBarLevel } = useTokensBar(
  computed(() => props.sessionInputTokens), computed(() => props.inputTokenBudgetPerSession)
)
const {
  visible: tokensTooltipVisible, style: tokensTooltipStyle, show: showTokensTooltip, hide: hideTokensTooltip
} = useFloatingTooltip()
</script>

<template>
  <div
    class="session-detail-card"
    :class="{ 'session-detail-card-clickable': editable }"
    :title="editable ? 'Click to open' : null"
    @click="toggle"
  >
    <Transition name="crossfade" mode="out-in">
      <div v-if="editable && expanded" key="edit" class="session-detail-form">
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
          <CardMenu v-if="deletable && isImported">
            <button type="button" class="card-menu-item-danger" @click="emit('delete', session)">Delete</button>
          </CardMenu>
        </div>
        <span v-if="isImported" class="session-detail-badge session-detail-badge-neutral">Imported</span>
        <template v-if="hasTimestamps">
          <label class="session-detail-form-label">Started</label>
          <p class="session-detail-value">{{ formatSessionTimestamp(session.datetime_start) }}</p>
          <label class="session-detail-form-label">Ended</label>
          <p class="session-detail-value">{{ formatSessionTimestamp(session.datetime_end) }}</p>
        </template>
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
          <CardMenu v-if="deletable && isImported">
            <button type="button" class="card-menu-item-danger" @click="emit('delete', session)">Delete</button>
          </CardMenu>
        </div>
        <span v-if="isImported" class="session-detail-badge session-detail-badge-neutral">Imported</span>
        <span v-if="session.comment" class="session-detail-comment-preview">{{ session.comment }}</span>
        <div v-if="sessionInputTokens != null && inputTokenBudgetPerSession != null" class="session-detail-tokens">
          <span class="session-detail-tokens-label">Input tokens</span>
          <div
            class="session-detail-tokens-bar-track"
            @mouseenter="showTokensTooltip($event.currentTarget)"
            @mouseleave="hideTokensTooltip"
          >
            <div
              class="session-detail-tokens-bar-fill"
              :class="`session-detail-tokens-bar-fill-${tokensBarLevel}`"
              :style="{ width: tokensBarWidth }"
            ></div>
          </div>
        </div>
      </div>
    </Transition>
    <Teleport to="body">
      <span
        v-if="tokensTooltipVisible"
        class="session-detail-tokens-tooltip-floating"
        :style="tokensTooltipStyle"
      >{{ sessionInputTokens }} / {{ inputTokenBudgetPerSession }} input tokens</span>
    </Teleport>
  </div>
</template>

<style scoped>
.session-detail-card {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  margin: 0.75rem 0;
  padding: 0.6rem 0.75rem;
  border-radius: 8px;
  border: 1px solid #eee;
  background: #fafafa;
}

.session-detail-card-clickable {
  cursor: pointer;
}

.session-detail-card-clickable:hover {
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

.session-detail-badge-neutral {
  background: #4a6fa5;
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

.session-detail-tokens {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin: 0.4rem 0 0;
}

.session-detail-tokens-label {
  flex-shrink: 0;
  font-size: 0.72rem;
  color: #888;
}

.session-detail-tokens-bar-track {
  position: relative;
  flex: 1;
  min-width: 40px;
  height: 8px;
  border-radius: 999px;
  background: #eee;
  overflow: hidden;
  cursor: default;
}

.session-detail-tokens-bar-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.3s ease;
}

.session-detail-tokens-bar-fill-green { background: #2e7d32; }
.session-detail-tokens-bar-fill-orange { background: #f5a623; }
.session-detail-tokens-bar-fill-red { background: #c62828; }

.crossfade-enter-active,
.crossfade-leave-active {
  transition: opacity 0.15s ease;
}

.crossfade-enter-from,
.crossfade-leave-to {
  opacity: 0;
}
</style>

<style>
/* FIXME: unscoped on purpose — teleported to <body>, outside scoped CSS reach. */
.session-detail-tokens-tooltip-floating {
  position: fixed;
  width: max-content;
  max-width: 200px;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  background: #333;
  color: white;
  font-size: 0.72rem;
  font-weight: 400;
  line-height: 1.3;
  text-align: left;
  pointer-events: none;
  z-index: 1000;
}
</style>
