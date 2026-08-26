<script setup>
// Full-viewport gate shown between a successful login and the app itself,
// for a session that authenticated but has no User row yet (see
// App.vue's own pingBackend — a 403 off GET /api/state is the signal).
// Same visual register as LoginView.vue. Accept creates the row
// (postAcceptTerms) and lets App.vue resume booting; Reject logs out
// with no row ever created — no trace of the attempt.
//
// Also reused as-is by ChatWindow.vue whenever a project's own
// legal/terms.md changed since this user's last live session there (see
// chatStoreFactory.js's legalTermsPending) — showReject=false there since
// there's nothing to decline, just an Accept to dismiss it.
import { onMounted, ref } from 'vue'
import { getTerms } from '../api.js'
import { renderMarkdown } from '../markdown.js'

const props = defineProps({
  // False for ChatWindow.vue's own reuse of this component (a live
  // session's legal/terms.md changed — an FYI notice, not a consent gate
  // the user can decline) — the platform-level TermsView caller below
  // leaves this at its default, since that one's Reject really does log
  // the session out with no User row ever created.
  showReject: { type: Boolean, default: true },
  // Defaults to the platform-wide GET /api/terms; ChatWindow.vue passes
  // its own fetcher instead, reading a project's own legal/terms.md
  // pinned to the session that triggered this screen.
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

function accept() {
  emit('accept')
}

function reject() {
  emit('reject')
}
</script>

<template>
  <div class="terms-view">
    <div class="terms-panel">
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
  background: white;
  font-family: system-ui, -apple-system, sans-serif;
  z-index: 1000;
  padding: 2rem;
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
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 1rem 1.4rem;
  border: 1px solid #ddd;
  border-radius: 10px;
  font-size: 0.9rem;
  line-height: 1.6;
  color: #333;
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
