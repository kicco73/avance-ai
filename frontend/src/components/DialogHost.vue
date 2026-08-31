<script setup>
// Single native <dialog> that renders whatever dialogStore.js's
// activeDialog currently is — confirm/prompt/choose/info/about/custom,
// mutually exclusive by construction (dialogStore.js only ever hands
// this one request at a time). showModal()/close() are driven by the
// watch below; everything about focus trapping, ESC handling, and
// focus-return on close is the browser's own <dialog> behavior, not
// reimplemented here.
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
</script>

<template>
  <div class="app-dim" aria-hidden="true" :class="{ 'app-dim-active': activeDialog }"></div>

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
      <button
        type="button"
        class="dialog-close-btn"
        title="Close"
        @click="closeWith(cancelValueFor(activeDialog))"
      >×</button>

      <template v-if="activeDialog.kind === 'about'">
        <img :src="logoUrl" class="dialog-about-logo" alt="Avance" />
        <p class="dialog-about-version">Version {{ activeDialog.version }}</p>
      </template>
      <template v-else-if="activeDialog.kind === 'custom'">
        <component :is="activeDialog.component" v-bind="activeDialog.props" />
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

      <!-- about/custom have no buttons of their own — the × above is the
           only way to close them. Same for info, unless its own caller
           opted into a single labeled button via okLabel (e.g. "Bye!"). -->
      <div
        v-if="['confirm', 'prompt', 'choose'].includes(activeDialog.kind) || (activeDialog.kind === 'info' && activeDialog.okLabel)"
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

        <template v-else-if="activeDialog.kind === 'info'">
          <button class="dialog-btn dialog-btn-primary" @click="closeWith(true)">{{ activeDialog.okLabel }}</button>
        </template>
      </div>
    </div>
  </dialog>
</template>

<style scoped>
/* The dialog's own dim/scrim, painted here rather than on .app-dialog's
   own ::backdrop (see that rule's own comment) for two reasons found
   together: ::backdrop's opacity transition + @starting-style gives it
   an animated-opacity compositing layer, which WebKit clips to the
   (short, on standalone iOS) viewport regardless of any bottom
   extension — same failure mode TermsView.vue's own root ran into (see
   App.vue's .app-backdrop comment for the general rule); and custom
   property inheritance into ::backdrop isn't reliable in the first
   place, so var(--viewport-bottom-overshoot) could easily have resolved
   the 0px fallback there regardless. This element is a real, ordinary
   box instead — always mounted (so its own transition can actually run
   both ways, in and out) and toggled by .app-dim-active. Its own
   dissolve animates background-color rather than opacity, which never
   promotes a compositing layer, so the bottom extension below actually
   holds. Never give this element opacity or transform of its own. z-index
   above .dialog-card but below nothing that matters — the <dialog> this
   dims sits in the browser's own top layer regardless, always above.
   pointer-events: none — click-through is deliberate; onBackdropClick
   still needs the (now fully transparent) ::backdrop to catch the
   click-outside-to-close hit-test, which this element doesn't touch. */
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
  transform: scale(1) translateY(0);
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
  padding-right: 1.6rem; /* clears the × close button, top-right */
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
/* Genuinely unscoped — ::backdrop belongs to the top-layer <dialog>
   renders into, never reachable by this component's own [data-v-xxx]
   scoping regardless of which <style> block it's declared in. Kept
   transparent, deliberately minimal: the actual dim/scrim is .app-dim
   above now (see its own comment for why), so this is only ever a
   hit-test surface for onBackdropClick's click-outside-to-close — no
   visible fill, no transition, nothing animated here at all. */
.app-dialog::backdrop {
  background: transparent;
}
</style>
