<script setup>
// The shared error strip — one instance per screen (App.vue for the main
// page, EditProjectView.vue/BenchmarkProjectView.vue for their own
// full-screen overlays, SplashScreen.vue for the boot-failed state),
// each mounted immediately below that screen's own toolbar. Every
// apiFetch failure (see api.js) lands in the same errorStore.js regardless
// of which screen triggered it, so this is the one place that ever needs
// to render it — no props, no per-screen copy of this markup to keep in
// sync.
import { ref, watch } from 'vue'
import { errorDetail, errorMessage } from '../errorStore.js'

const showDetail = ref(false)

// A new error replaces whatever was being inspected — stale expanded
// detail from a previous, unrelated failure would otherwise linger open.
watch(errorMessage, () => {
  showDetail.value = false
})
</script>

<template>
  <div v-if="errorMessage" class="error-banner-wrap">
    <div class="error-banner-row">
      <p class="error-banner-message">{{ errorMessage }}</p>
      <button
        v-if="errorDetail"
        type="button"
        class="error-banner-details-btn"
        @click="showDetail = !showDetail"
      >
        {{ showDetail ? 'Hide details' : 'Details' }}
      </button>
    </div>
    <pre v-if="errorDetail && showDetail" class="error-banner-detail">{{ errorDetail }}</pre>
  </div>
</template>

<style scoped>
.error-banner-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 1rem;
  background: #fdecea;
  border-bottom: 1px solid #f5c6c2;
}

.error-banner-message {
  margin: 0;
  color: #c62828;
  font-size: 0.9rem;
  flex: 1;
}

.error-banner-details-btn {
  flex: none;
  padding: 0.2rem 0.6rem;
  border-radius: 6px;
  border: 1px solid #c62828;
  background: white;
  color: #c62828;
  cursor: pointer;
  font-size: 0.8rem;
}

.error-banner-detail {
  margin: 0;
  padding: 0.75rem 1rem;
  background: #fdecea;
  border-bottom: 1px solid #f5c6c2;
  color: #7a1f1f;
  font-size: 0.8rem;
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
}
</style>
