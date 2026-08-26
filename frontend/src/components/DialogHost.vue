<script setup>
// Single native <dialog> that renders whatever dialogStore.js's
// activeDialog currently is — confirm/prompt/choose/info, mutually
// exclusive by construction (dialogStore.js only ever hands this one
// request at a time). showModal()/close() are driven by the watch below;
// everything about focus trapping, ESC handling, and focus-return on
// close is the browser's own <dialog> behavior, not reimplemented here.
import { computed, nextTick, ref, watch } from 'vue'
import { activeDialog, resolveActiveDialog } from '../dialogStore.js'
import logoUrl from '../assets/avance-logo.png'

const CLOSE_ANIMATION_MS = 180

const dialogEl = ref(null)
const inputEl = ref(null)
// Toggles the card's own enter/leave class — separate from the <dialog>
// element's open/closed state so the close path can play the exit
// transition *before* the native close() actually fires (see closeWith).
const cardVisible = ref(false)

const promptValue = ref('')

const promptError = computed(() => {
  const dialog = activeDialog.value
  if (!dialog || dialog.kind !== 'prompt' || !dialog.validate) return ''
  return dialog.validate(promptValue.value) || ''
})

// The value closeWith() is mid-way through resolving with — read by the
// native 'close' listener once close() actually fires, since that event
// carries no payload of its own.
let pendingResult

watch(activeDialog, async (dialog) => {
  if (!dialog) return
  promptValue.value = dialog.kind === 'prompt' ? (dialog.initialValue ?? '') : ''
  await nextTick()
  dialogEl.value?.showModal()
  if (dialog.kind === 'prompt') inputEl.value?.focus()
  // Mounts in its "from" state first (opacity: 0 / scaled down, see
  // .dialog-card below) — flipping the class on the next frame is what
  // actually makes the enter transition play instead of starting already
  // in its "to" state.
  requestAnimationFrame(() => { cardVisible.value = true })
})

// Every closing interaction (button, backdrop click, ESC) funnels through
// here, so there's exactly one place that plays the exit transition and
// then hands off to the native close() — which is what actually resolves
// the promise (see onNativeClose) and restores focus to the opener.
function closeWith(value) {
  if (!cardVisible.value) return // already closing
  pendingResult = value
  cardVisible.value = false
  setTimeout(() => dialogEl.value?.close(), CLOSE_ANIMATION_MS)
}

function onNativeClose() {
  resolveActiveDialog(pendingResult)
}

// ESC fires 'cancel' (cancelable) before the native close — prevented so
// closeWith's own animated sequence runs instead of an instant vanish,
// while still treating ESC as an ordinary cancel.
function onCancel(event) {
  event.preventDefault()
  closeWith(cancelValueFor(activeDialog.value))
}

// A click that lands on the <dialog> element itself (not a descendant) is
// a click on its ::backdrop — there's no other way to hit-test that area.
function onBackdropClick(event) {
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

function closeInfo() {
  closeWith(true)
}
</script>

<template>
  <dialog
    v-if="activeDialog"
    ref="dialogEl"
    class="app-dialog"
    @cancel="onCancel"
    @close="onNativeClose"
    @click="onBackdropClick"
  >
    <div
      class="dialog-card"
      :class="{ 'dialog-card-visible': cardVisible, 'dialog-card-about': activeDialog.kind === 'about' }"
    >
      <template v-if="activeDialog.kind === 'about'">
        <img :src="logoUrl" class="dialog-about-logo" alt="Avance" />
        <p class="dialog-about-version">Version {{ activeDialog.version }}</p>
      </template>
      <template v-else>
        <h2 class="dialog-title">{{ activeDialog.title }}</h2>
        <p v-if="activeDialog.body" class="dialog-body">{{ activeDialog.body }}</p>
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

      <div class="dialog-actions">
        <template v-if="activeDialog.kind === 'confirm'">
          <button class="dialog-btn dialog-btn-cancel" @click="closeWith(false)">Cancel</button>
          <button
            class="dialog-btn dialog-btn-primary"
            :class="{ 'dialog-btn-danger': activeDialog.danger }"
            @click="confirmOk"
          >{{ activeDialog.okLabel }}</button>
        </template>

        <template v-else-if="activeDialog.kind === 'prompt'">
          <button class="dialog-btn dialog-btn-cancel" @click="closeWith(null)">Cancel</button>
          <button class="dialog-btn dialog-btn-primary" :disabled="!!promptError" @click="submitPrompt">OK</button>
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

        <template v-else-if="activeDialog.kind === 'info' || activeDialog.kind === 'about'">
          <button class="dialog-btn dialog-btn-primary" @click="closeInfo">Close</button>
        </template>
      </div>
    </div>
  </dialog>
</template>

<style scoped>
/* Unscoped rules below target ::backdrop (a scoped [data-v-xxx] attribute
   selector can't reach a pseudo-element) and the <dialog> element's own
   default UA styling, which needs resetting before .dialog-card's own
   padding/sizing applies. */
.app-dialog {
  padding: 0;
  border: none;
  border-radius: 10px;
  background: transparent;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
  max-width: 420px;
  width: calc(100vw - 2rem);
}

.dialog-card {
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
  transform: scale(1) translateY(0);
}

.dialog-title {
  margin: 0 0 0.5rem;
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
/* Genuinely unscoped — ::backdrop belongs to the top-layer <dialog>
   renders into, never reachable by this component's own [data-v-xxx]
   scoping regardless of which <style> block it's declared in. */
.app-dialog::backdrop {
  background: rgba(0, 0, 0, 0.35);
  opacity: 1;
  transition: opacity 0.18s ease;
}

@starting-style {
  .app-dialog[open]::backdrop {
    opacity: 0;
  }
}
</style>
