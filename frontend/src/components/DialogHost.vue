<script setup>
import { computed, nextTick, provide, ref, watch } from 'vue'
import { activeDialog, resolveActiveDialog } from '../dialogStore.js'
import { renderMarkdown } from '../markdown.js'
import logoUrl from '../assets/avance-logo.png'

const CLOSE_ANIMATION_MS = 180
const DOC_READING_WIDTH = 'min(68ch, 90vw)'

const dialogEl = ref(null)
const inputEl = ref(null)
const cardVisible = ref(false)

const promptValue = ref('')

const TEXT_INPUT_KINDS = ['prompt', 'textarea']

const promptError = computed(() => {
  const dialog = activeDialog.value
  if (!dialog || !TEXT_INPUT_KINDS.includes(dialog.kind) || !dialog.validate) return ''
  return dialog.validate(promptValue.value) || ''
})

let pendingResult

function measuredDocWidth() {
  const card = dialogEl.value?.querySelector('.dialog-card')
  if (!card) return null
  const prevWidth = card.style.width
  const prevMaxWidth = card.style.maxWidth
  card.style.width = 'fit-content'
  card.style.maxWidth = DOC_READING_WIDTH
  const width = Math.ceil(card.getBoundingClientRect().width)
  card.style.width = prevWidth
  card.style.maxWidth = prevMaxWidth
  return width
}

watch(activeDialog, async (dialog) => {
  if (!dialog) return
  promptValue.value = TEXT_INPUT_KINDS.includes(dialog.kind) ? (dialog.initialValue ?? '') : ''
  await nextTick()
  dialogEl.value?.showModal()
  dialogEl.value.style.width = dialog.markdown ? `${measuredDocWidth()}px` : ''
  if (TEXT_INPUT_KINDS.includes(dialog.kind)) inputEl.value?.focus()
  requestAnimationFrame(() => { cardVisible.value = true })
})

function closeWith(value) {
  if (!cardVisible.value) return
  pendingResult = value
  cardVisible.value = false
  setTimeout(() => dialogEl.value?.close(), CLOSE_ANIMATION_MS)
}

function onNativeClose() {
  resolveActiveDialog(pendingResult)
}

provide('closeDialog', (value = null) => closeWith(value))

const dismissible = computed(() => activeDialog.value?.kind !== 'blocking')

function onCancel(event) {
  event.preventDefault()
  if (dismissible.value) closeWith(cancelValueFor(activeDialog.value))
}

function onBackdropClick(event) {
  if (!dismissible.value) return
  if (event.target === dialogEl.value) closeWith(cancelValueFor(activeDialog.value))
}

function cancelValueFor(dialog) {
  return dialog?.kind === 'confirm' ? false : null
}

function confirmOk() {
  closeWith(true)
}

function submitPrompt() {
  if (promptError.value) return
  closeWith(promptValue.value)
}

function chooseOption(id) {
  closeWith(id)
}
</script>

<template>
  <div class="app-dim" aria-hidden="true" :class="{ 'app-dim-active': activeDialog }"></div>

  <dialog
    v-if="activeDialog"
    ref="dialogEl"
    class="app-dialog"
    :class="{ 'app-dialog-wide': activeDialog.wide, 'app-dialog-doc': activeDialog.markdown }"
    @cancel="onCancel"
    @close="onNativeClose"
    @click="onBackdropClick"
  >
    <div
      class="dialog-card"
      :class="{ 'dialog-card-visible': cardVisible, 'dialog-card-about': activeDialog.kind === 'about' }"
    >
      <button
        v-if="dismissible"
        type="button"
        class="dialog-close-btn"
        title="Close"
        @click="closeWith(cancelValueFor(activeDialog))"
      >×</button>

      <template v-if="activeDialog.kind === 'about'">
        <img :src="logoUrl" class="dialog-about-logo" alt="Avance" />
        <p class="dialog-about-version">Version {{ activeDialog.version }}</p>
      </template>
      <template v-else-if="activeDialog.kind === 'custom' || activeDialog.kind === 'blocking'">
        <component :is="activeDialog.component" :key="activeDialog.id" v-bind="activeDialog.props" />
      </template>
      <template v-else>
        <h2 v-if="activeDialog.title" class="dialog-title">{{ activeDialog.title }}</h2>
        <div
          v-if="activeDialog.body && activeDialog.markdown"
          class="dialog-body dialog-body-markdown"
          v-html="renderMarkdown(activeDialog.body)"
        ></div>
        <p v-else-if="activeDialog.body" class="dialog-body">{{ activeDialog.body }}</p>
      </template>

      <template v-if="activeDialog.kind === 'prompt'">
        <input
          ref="inputEl"
          v-model="promptValue"
          type="text"
          class="dialog-input"
          :class="{ 'dialog-input-invalid': promptError }"
          :placeholder="activeDialog.placeholder"
          @keydown.enter="submitPrompt"
        />
        <p v-if="promptError" class="dialog-field-error">{{ promptError }}</p>
      </template>

      <template v-if="activeDialog.kind === 'textarea'">
        <textarea
          ref="inputEl"
          v-model="promptValue"
          class="dialog-textarea"
          :class="{ 'dialog-input-invalid': promptError }"
          :placeholder="activeDialog.placeholder"
        ></textarea>
        <p v-if="promptError" class="dialog-field-error">{{ promptError }}</p>
      </template>

      <div
        v-if="[...TEXT_INPUT_KINDS, 'confirm', 'choose'].includes(activeDialog.kind) || (activeDialog.kind === 'info' && activeDialog.okLabel)"
        class="dialog-actions"
      >
        <template v-if="activeDialog.kind === 'confirm'">
          <button class="dialog-btn dialog-btn-cancel" @click="closeWith(false)">Cancel</button>
          <button
            class="dialog-btn dialog-btn-primary"
            :class="{ 'dialog-btn-danger': activeDialog.danger }"
            @click="confirmOk"
          >{{ activeDialog.okLabel }}</button>
        </template>

        <template v-else-if="TEXT_INPUT_KINDS.includes(activeDialog.kind)">
          <button class="dialog-btn dialog-btn-cancel" @click="closeWith(null)">Cancel</button>
          <button class="dialog-btn dialog-btn-primary" :disabled="!!promptError" @click="submitPrompt">{{ activeDialog.okLabel ?? 'OK' }}</button>
        </template>

        <template v-else-if="activeDialog.kind === 'choose'">
          <button class="dialog-btn dialog-btn-cancel" @click="closeWith(null)">Cancel</button>
          <button
            v-for="option in activeDialog.options"
            :key="option.id"
            class="dialog-btn dialog-btn-primary"
            :class="{ 'dialog-btn-danger': option.danger }"
            @click="chooseOption(option.id)"
          >{{ option.label }}</button>
        </template>

        <template v-else-if="activeDialog.kind === 'info'">
          <button class="dialog-btn dialog-btn-primary" @click="closeWith(true)">{{ activeDialog.okLabel }}</button>
        </template>
      </div>
    </div>
  </dialog>
</template>

<style scoped>
.app-dim {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  z-index: 2000;
  pointer-events: none;
  background: transparent;
  transition: background-color 0.18s ease;
}

.app-dim-active {
  background: rgba(0, 0, 0, 0.35);
}

.app-dialog {
  padding: 0;
  border: none;
  border-radius: 10px;
  background: transparent;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
  max-width: 420px;
  width: calc(100vw - 2rem);
}

.app-dialog-wide {
  max-width: 640px;
}

.app-dialog-doc {
  min-width: min(420px, 90vw);
  max-width: 90vw;
}

.dialog-card {
  position: relative;
  background: white;
  border: 1px solid #ddd;
  border-radius: 10px;
  padding: 1.2rem 1.4rem;
  opacity: 0;
  transform: scale(0.94) translateY(6px);
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.dialog-card-visible {
  opacity: 1;
  /* FIXME: transform: none, not scale(1) translateY(0) — any non-none
     transform value breaks position: fixed for descendants (e.g.
     TriggerEditor.vue's completion tooltip), which is why it was clipped. */
  transform: none;
}

.dialog-close-btn {
  position: absolute;
  top: 0.6rem;
  right: 0.6rem;
  width: 1.8rem;
  height: 1.8rem;
  border: none;
  border-radius: 6px;
  background: none;
  color: #777;
  font-size: 1.3rem;
  line-height: 1;
  cursor: pointer;
}

.dialog-close-btn:hover {
  background: #f0f0f0;
}

.dialog-title {
  margin: 0 0 0.5rem;
  padding-right: 1.6rem;
  font-size: 1.05rem;
  font-weight: 600;
  color: #333;
}

.dialog-body {
  margin: 0 0 0.8rem;
  font-size: 0.88rem;
  line-height: 1.5;
  color: #444;
  white-space: pre-line;
}

.dialog-body-markdown {
  white-space: normal;
}

.dialog-body-markdown :deep(p) {
  margin: 0 0 0.8rem;
}

.dialog-body-markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.dialog-body-markdown :deep(h1),
.dialog-body-markdown :deep(h2),
.dialog-body-markdown :deep(h3),
.dialog-body-markdown :deep(h4),
.dialog-body-markdown :deep(h5),
.dialog-body-markdown :deep(h6) {
  margin: 0.8rem 0 0.5rem;
  line-height: 1.3;
}

.dialog-body-markdown :deep(h1:first-child),
.dialog-body-markdown :deep(h2:first-child),
.dialog-body-markdown :deep(h3:first-child),
.dialog-body-markdown :deep(h4:first-child) {
  margin-top: 0;
}

.dialog-body-markdown :deep(ul),
.dialog-body-markdown :deep(ol) {
  margin: 0.5rem 0;
  padding-left: 1.5rem;
}

.dialog-body-markdown :deep(li) {
  margin: 0.25rem 0;
}

.dialog-body-markdown :deep(blockquote) {
  margin: 0.75rem 0;
  padding: 0.2rem 0 0.2rem 1rem;
  border-left: 4px solid #bbb;
  color: #666;
}

.dialog-body-markdown :deep(hr) {
  border: none;
  border-top: 1px solid #ccc;
  margin: 1rem 0;
}

.dialog-body-markdown :deep(pre) {
  overflow-x: auto;
  contain: inline-size;
  margin: 0.75rem 0;
  padding: 0.9rem;
  border-radius: 8px;
  background: #1e1e1e;
  color: #f8f8f2;
}

.dialog-body-markdown :deep(pre code) {
  background: transparent;
  color: inherit;
  padding: 0;
  border-radius: 0;
}

.dialog-body-markdown :deep(code) {
  font-family: Consolas, Monaco, Menlo, monospace;
  font-size: 0.9em;
}

.dialog-body-markdown :deep(:not(pre) > code) {
  background: rgba(0, 0, 0, 0.08);
  padding: 0.12rem 0.35rem;
  border-radius: 4px;
}

.dialog-body-markdown :deep(.md-table-wrap) {
  overflow-x: auto;
  contain: inline-size;
  margin: 0.75rem 0;
}

.dialog-body-markdown :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0;
}

.dialog-body-markdown :deep(th),
.dialog-body-markdown :deep(td) {
  border: 1px solid #ccc;
  padding: 0.45rem 0.6rem;
  text-align: left;
}

.dialog-body-markdown :deep(th) {
  background: rgba(0, 0, 0, 0.05);
}

.dialog-body-markdown :deep(img) {
  max-width: 100%;
  border-radius: 6px;
  -webkit-user-drag: none;
}

.dialog-body-markdown :deep(a) {
  color: inherit;
  text-decoration: underline;
}

.dialog-body-markdown :deep(strong) {
  font-weight: 600;
}

.dialog-body-markdown :deep(em) {
  font-style: italic;
}

.dialog-input {
  display: block;
  width: 100%;
  box-sizing: border-box;
  padding: 0.45rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  font: inherit;
  font-size: 0.88rem;
}

.dialog-input:focus {
  outline: none;
  border-color: #4a6fa5;
}

.dialog-input-invalid {
  border-color: #c62828;
}

.dialog-textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  min-height: 7rem;
  padding: 0.45rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  font: inherit;
  font-size: 0.88rem;
  line-height: 1.4;
  resize: vertical;
}

.dialog-textarea:focus {
  outline: none;
  border-color: #4a6fa5;
}

.dialog-field-error {
  margin: 0.35rem 0 0;
  font-size: 0.78rem;
  color: #c62828;
}

.dialog-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 1.1rem;
}

.dialog-card-about {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.dialog-card-about .dialog-actions {
  justify-content: center;
  width: 100%;
}

.dialog-about-logo {
  width: 96px;
  height: auto;
  animation: dialog-about-logo-in 3s ease-out;
}

@keyframes dialog-about-logo-in {
  from {
    opacity: 0;
    transform: scale(1.15);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.dialog-about-version {
  margin: 0.8rem 0 0;
  font-size: 0.85rem;
  color: #777;
}

.dialog-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #ccc;
  background: white;
  color: #333;
  font-size: 0.85rem;
  cursor: pointer;
}

.dialog-btn-cancel:hover {
  background: #f0f0f0;
}

.dialog-btn-primary {
  border-color: #4a6fa5;
  background: #4a6fa5;
  color: white;
}

.dialog-btn-primary:hover:not(:disabled) {
  background: #3d5c8a;
}

.dialog-btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.dialog-btn-danger {
  border-color: #c62828;
  background: #c62828;
}

.dialog-btn-danger:hover:not(:disabled) {
  background: #a82121;
}
</style>

<style>
.app-dialog::backdrop {
  background: transparent;
}
</style>
