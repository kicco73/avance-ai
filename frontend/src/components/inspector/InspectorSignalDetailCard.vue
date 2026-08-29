<script setup>
defineProps({
  // { name, ui_label, ui_description, definition } | null — see
  // EditProjectView.vue's autoSelectedSignal.
  signal: { type: Object, default: null }
})
</script>

<template>
  <div class="signal-detail-card-wrap">
    <p v-if="!signal" class="signal-detail-empty">Select a signal to see its details.</p>
    <div v-else class="signal-detail-card">
      <div class="signal-detail-header">
        <span class="signal-detail-badge">Signal</span>
        <span class="signal-detail-name">{{ signal.ui_label || signal.name }}</span>
      </div>
      <p v-if="signal.ui_description" class="signal-detail-description">{{ signal.ui_description }}</p>
      <div v-if="signal.definition" class="signal-detail-field">
        <span class="signal-detail-field-label">
          <span class="signal-detail-ai-icon" title="Read by the AI">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
          </span>
          Definition
        </span>
        <p class="signal-detail-definition">{{ signal.definition }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.signal-detail-card-wrap { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.signal-detail-empty { margin: auto; padding: 1rem; font-size: 0.85rem; color: #777; text-align: center; }
.signal-detail-card { padding: 0.6rem 0.75rem; overflow-y: auto; }
.signal-detail-header { display: flex; align-items: center; gap: 0.4rem; }
.signal-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; background: #6a4c93; }
.signal-detail-name { flex: 1; min-width: 0; font-weight: 600; font-size: 0.95rem; color: #222; }
.signal-detail-description { margin: 0.5rem 0 0; font-size: 0.85rem; color: #666; line-height: 1.4; }
.signal-detail-field { margin-top: 0.9rem; padding-top: 0.7rem; border-top: 1px solid #eee; }
.signal-detail-field-label { display: flex; align-items: center; gap: 0.35rem; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
.signal-detail-ai-icon { display: inline-flex; flex-shrink: 0; color: #8b5cf6; }
.signal-detail-definition { margin: 0.3rem 0 0; font-size: 0.82rem; color: #555; line-height: 1.5; white-space: pre-wrap; }
</style>
