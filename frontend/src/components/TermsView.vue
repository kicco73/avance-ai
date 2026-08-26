<script setup>
// Full-viewport gate shown between a successful login and the app itself,
// for a session that authenticated but has no User row yet (see
// App.vue's own pingBackend — a 403 off GET /api/state is the signal).
// Same visual register as LoginView.vue. Accept creates the row
// (postAcceptTerms) and lets App.vue resume booting; Reject logs out
// with no row ever created — no trace of the attempt.
//
// Also reused as-is by LiveChatWindow.vue whenever a project's own
// legal/terms.md is pending acceptance for the active user — showReject=
// false there since there's nothing to decline, just an Accept to dismiss it.
import { onMounted, ref } from 'vue'
import { getTerms } from '../api.js'
import { renderMarkdown } from '../markdown.js'
import logoUrl from '../assets/avance-logo.png'

const props = defineProps({
  // False for LiveChatWindow.vue's own reuse of this component (a
  // project's legal/terms.md pending acceptance — not a consent gate the
  // user can decline) — the platform-level TermsView caller below leaves
  // this at its default, since that one's Reject really does log the
  // session out with no User row ever created.
  showReject: { type: Boolean, default: true },
  // Defaults to the platform-wide GET /api/terms; LiveChatWindow.vue passes
  // its own fetcher instead, reading the project's own legal/terms.md.
  fetchTerms: { type: Function, default: getTerms }
})

const emit = defineEmits(['accept', 'reject'])

const loading = ref(true)
const error = ref('')
const content = ref('')

onMounted(async () => {
  try {
    const result = await props.fetchTerms()
    content.value = result.content
  } catch (err) {
    error.value = err.message || 'Failed to load the Terms of Service.'
  } finally {
    loading.value = false
  }
})

const CLOSE_ANIMATION_MS = 300
const closing = ref(false)

function accept() {
  if (closing.value) return
  closing.value = true
  setTimeout(() => emit('accept'), CLOSE_ANIMATION_MS)
}

function reject() {
  if (closing.value) return
  closing.value = true
  setTimeout(() => emit('reject'), CLOSE_ANIMATION_MS)
}
</script>

<template>
  <div class="terms-view" :class="{ 'terms-view-closing': closing }">
    <div class="terms-panel" :style="{ '--terms-watermark': `url(${logoUrl})` }">
      <h1 class="terms-title">Terms of Service</h1>

      <p v-if="loading" class="terms-status">Loading…</p>
      <p v-else-if="error" class="terms-status terms-error">{{ error }}</p>
      <div v-else class="terms-content" v-html="renderMarkdown(content)"></div>

      <div class="terms-actions">
        <button v-if="showReject" type="button" class="terms-btn terms-btn-reject" @click="reject">Reject</button>
        <button type="button" class="terms-btn terms-btn-accept" autofocus :disabled="loading" @click="accept">Accept</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.terms-view {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-base-gradient);
  font-family: system-ui, -apple-system, sans-serif;
  z-index: 1000;
  padding: 2rem;
  opacity: 1;
  transform: scale(1);
  transition: opacity 0.3s ease-in, transform 0.3s ease-in;
  animation: terms-view-in 0.35s ease-out;
}

@keyframes terms-view-in {
  from {
    opacity: 0;
    transform: scale(0.96);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.terms-view-closing {
  opacity: 0;
  transform: scale(0.94);
}

.terms-panel {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 760px;
  height: 100%;
  max-height: 100%;
}

.terms-title {
  flex-shrink: 0;
  margin: 0 0 1rem;
  font-size: 1.3rem;
  font-weight: 600;
  color: #4a6fa5;
  letter-spacing: 0.02em;
}

.terms-status {
  margin: 0;
  font-size: 0.9rem;
  color: #777;
}

.terms-error {
  color: #c62828;
}

.terms-content {
  position: relative;
  z-index: 0;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem 1.4rem;
  border: 1px solid #ddd;
  border-radius: 10px;
  background: white;
  font-size: 0.9rem;
  line-height: 1.6;
  color: #313b4a;
}

.terms-content::before {
  content: '';
  position: absolute;
  inset: 0;
  background-image: var(--terms-watermark);
  background-repeat: no-repeat;
  background-position: calc(100% - 30px) 30px;
  background-size: 31.5%;
  opacity: 0.07;
  pointer-events: none;
  z-index: -1;
}

.terms-content :is(h1, h2, h3, h4) {
  margin: 1rem 0 0.4rem;
  line-height: 1.3;
}

.terms-content h1:first-child,
.terms-content h2:first-child {
  margin-top: 0;
}

.terms-content h1 { font-size: 1.15rem; }
.terms-content h2 { font-size: 1.05rem; }
.terms-content h3 { font-size: 0.95rem; }

.terms-content p {
  margin: 0 0 0.6rem;
}

.terms-content ul,
.terms-content ol {
  margin: 0 0 0.6rem;
  padding-left: 1.3rem;
}

.terms-actions {
  flex-shrink: 0;
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  margin-top: 1.25rem;
}

.terms-btn {
  padding: 0.55rem 1.4rem;
  border-radius: 8px;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
}

.terms-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.terms-btn-reject {
  border: 1px solid #ccc;
  background: white;
  color: #555;
}

.terms-btn-reject:hover:not(:disabled) {
  background: #f5f5f5;
}

/* Default action, per the product spec: Accept is the primary button. */
.terms-btn-accept {
  border: 1px solid #4a6fa5;
  background: #4a6fa5;
  color: white;
}

.terms-btn-accept:hover:not(:disabled) {
  background: #3d5c8a;
}
</style>
