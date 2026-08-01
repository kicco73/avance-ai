<script setup>
// The sessions list content (header + rows) shared by every chat surface
// that lets a user pick a past/present session — the main page and the
// "Edit project" view's embedded chat (both via ChatWindow.vue) and the
// "Benchmark project" view (BenchmarkProjectView.vue), which reviews a
// session read-only and so never creates/deletes one (see
// allowCreate/allowDelete). Layout (the sliding wrap, its width, the drag
// divider) stays each parent's own concern, same as Inspector.vue's own
// width — this component is just the list itself.
const props = defineProps({
  sessions: { type: Array, required: true },
  loading: { type: Boolean, default: false },
  currentSessionId: { type: [Number, String], default: null },
  // Which session (if any) a delete request is in flight for — disables
  // just that row's own delete button. Ignored when allowDelete is false.
  deletingSessionId: { type: [Number, String], default: null },
  allowCreate: { type: Boolean, default: true },
  allowDelete: { type: Boolean, default: true }
})

const emit = defineEmits(['select', 'create', 'delete'])

function formatSessionTimestamp(iso) {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}
</script>

<template>
  <div class="sessions-panel-header">
    <span>Sessions</span>
    <div v-if="allowCreate" class="sessions-panel-header-actions">
      <button type="button" class="sessions-panel-icon-btn" title="New session" @click="emit('create')">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
          <path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z" />
        </svg>
      </button>
    </div>
  </div>

  <p v-if="loading" class="sessions-status">Loading…</p>
  <p v-else-if="!sessions.length" class="sessions-status">No sessions yet.</p>

  <ul v-else class="sessions-list">
    <li v-for="session in sessions" :key="session.id" class="session-row">
      <button
        type="button"
        class="session-item"
        :class="{ 'session-item-active': session.id === currentSessionId }"
        @click="emit('select', session)"
      >
        <span class="session-badge" :class="{ 'session-badge-inactive': !session.active }">
          {{ session.end_state }}
        </span>
        <span class="session-timestamp">{{ formatSessionTimestamp(session.datetime_start) }}</span>
      </button>
      <button
        v-if="allowDelete"
        type="button"
        class="session-delete-btn"
        :disabled="deletingSessionId === session.id"
        title="Delete session"
        @click="emit('delete', session)"
      >
        &times;
      </button>
    </li>
  </ul>
</template>

<style scoped>
.sessions-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.6rem 0.9rem;
  border-bottom: 1px solid #ddd;
  font-weight: 600;
  font-size: 0.9rem;
  color: #333;
}

.sessions-panel-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.sessions-panel-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  padding: 0;
}

.sessions-panel-icon-btn:hover {
  background: #4a6fa5;
  color: white;
}

.sessions-status {
  margin: 0;
  padding: 0.75rem 0.9rem;
  font-size: 0.85rem;
  color: #666;
}

.sessions-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
  overflow-y: auto;
}

.session-row {
  display: flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0 0.4rem;
}

.session-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.3rem;
  flex: 1;
  min-width: 0;
  text-align: left;
  padding: 0.55rem 0.5rem;
  border: none;
  background: none;
  cursor: pointer;
}

.session-item:hover {
  background: #eef2f8;
}

.session-item-active {
  background: #e3ebf7;
}

.session-delete-btn {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #c62828;
  cursor: pointer;
  font-size: 1rem;
}

.session-delete-btn:hover:not(:disabled) {
  background: #fdecea;
}

.session-delete-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.session-badge {
  display: inline-block;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  background: #4a6fa5;
  color: white;
  font-size: 0.72rem;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.session-badge-inactive {
  background: #999;
  opacity: 0.5;
}

.session-timestamp {
  font-size: 0.75rem;
  color: #666;
}
</style>
