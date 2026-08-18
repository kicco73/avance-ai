<script setup>
import { ref } from 'vue'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'

// The sessions list content (header + rows) shared by every chat surface
// that lets a user pick a past/present session — the main page and the
// "Edit project" view's embedded chat (both via ChatWindow.vue) and the
// "Label sessions" view (BenchmarkProjectView.vue), which reviews a
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
  allowDelete: { type: Boolean, default: true },
  // BenchmarkProjectView's own — only an imported session (see
  // ChatSession.source) is ever deletable there, never a native one
  // (ChatWindow.vue's own live-chat contexts leave this at its default
  // false, since allowDelete there already means "any session").
  deleteImportedOnly: { type: Boolean, default: false },
  // BenchmarkProjectView's own — a transcript import produces a session
  // annotatable/testable without ever running live (see ChatSession.
  // source), meaningful only for review/labeling, not for the main chat
  // or the "Edit project" embedded chat (see ChatWindow.vue, which
  // leaves this at its default false).
  allowImport: { type: Boolean, default: false },
  // ChatWindow.vue's own live-chat contexts (main app + EditProjectView's
  // embedded chat) — an imported session can never become the live
  // conversation's own current/active session (see ChatSession.source),
  // so selecting one there must be a no-op, not a click that quietly
  // hands currentSessionId a session nothing downstream is prepared to
  // treat as live. BenchmarkProjectView leaves this at its default false:
  // reviewing/annotating an imported transcript is exactly its own
  // purpose, so selecting one there must keep working.
  restrictSelectionToNative: { type: Boolean, default: false },
  // Same always-mounted collapse/expand pattern as Inspector.vue's own
  // `collapsed` — the parent (ChatWindow.vue/BenchmarkProjectView.vue)
  // owns the actual width/layout collapse, this only owns its own
  // header's toggle button and hiding its own content while collapsed.
  collapsed: { type: Boolean, default: false }
})

const emit = defineEmits(['select', 'create', 'delete', 'import', 'update:collapsed'])

const importInput = ref(null)

function triggerImport() {
  importInput.value?.click()
}

function onImportFileChosen(event) {
  const file = event.target.files?.[0]
  if (file) emit('import', file)
  // Reset so choosing the exact same file again still fires 'change'.
  event.target.value = ''
}

function formatSessionTimestamp(iso) {
  if (!iso) return 'Timeline not available'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

function notSelectable(session) {
  return props.restrictSelectionToNative && session.source !== 'native'
}

function selectSession(session) {
  if (notSelectable(session)) return
  emit('select', session)
}

// The "has expert annotations" tag icon's own tooltip — one shared
// instance for the whole list (see useFloatingTooltip's own docstring on
// why a per-row template ref doesn't work inside v-for), since only one
// row can be hovered at a time anyway.
const {
  visible: annotationTooltipVisible,
  style: annotationTooltipStyle,
  show: showAnnotationTooltip,
  hide: hideAnnotationTooltip
} = useFloatingTooltip()
</script>

<template>
  <div class="sessions-panel-header">
    <span v-if="!collapsed" class="sessions-panel-title">Sessions</span>
    <div v-if="!collapsed && (allowCreate || allowImport)" class="sessions-panel-header-actions">
      <button v-if="allowImport" type="button" class="sessions-panel-icon-btn" title="Import transcript" @click="triggerImport">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
          <path d="M12 3l4 4h-3v6h-2V7H8l4-4zM5 19v-6h2v6h10v-6h2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z" />
        </svg>
      </button>
      <input v-if="allowImport" ref="importInput" type="file" accept=".txt,text/plain" class="sessions-panel-import-input" @change="onImportFileChosen" />
      <button v-if="allowCreate" type="button" class="sessions-panel-icon-btn" title="New session" @click="emit('create')">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
          <path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z" />
        </svg>
      </button>
    </div>
    <button
      class="collapse-toggle-btn"
      :title="collapsed ? 'Expand sessions' : 'Collapse sessions'"
      @click="emit('update:collapsed', !collapsed)"
    >{{ collapsed ? '◂' : '▸' }}</button>
  </div>

  <template v-if="!collapsed">
  <p v-if="loading" class="sessions-status">Loading…</p>
  <p v-else-if="!sessions.length" class="sessions-status">No sessions yet.</p>

  <ul v-else class="sessions-list">
    <li v-for="session in sessions" :key="session.id" class="session-row">
      <button
        type="button"
        class="session-item"
        :class="{ 'session-item-active': session.id === currentSessionId, 'session-item-disabled': notSelectable(session) }"
        :disabled="notSelectable(session)"
        :title="notSelectable(session) ? 'Imported sessions can\'t become the active conversation.' : null"
        @click="selectSession(session)"
      >
        <span class="session-badge-row">
          <span class="session-badge" :class="{ 'session-badge-inactive': !session.active }">
            {{ session.title || session.end_state }}
          </span>
          <span
            v-if="session.has_annotations"
            class="session-annotation-icon"
            tabindex="0"
            @mouseenter="showAnnotationTooltip($event.currentTarget)"
            @mouseleave="hideAnnotationTooltip"
            @focus="showAnnotationTooltip($event.currentTarget)"
            @blur="hideAnnotationTooltip"
            @click.stop
          >🏷</span>
        </span>
        <span class="session-timestamp">{{ formatSessionTimestamp(session.datetime_start) }}</span>
      </button>
      <button
        v-if="allowDelete && (!deleteImportedOnly || session.source === 'imported')"
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

  <Teleport to="body">
    <span v-if="annotationTooltipVisible" class="session-annotation-tooltip-floating" :style="annotationTooltipStyle">
      Has expert annotations
    </span>
  </Teleport>
</template>

<style scoped>
.sessions-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.6rem 0.9rem;
  border-bottom: 1px solid #ddd;
}

/* Same style as Inspector.vue's own .inspector-title. */
.sessions-panel-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
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

.collapse-toggle-btn {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #666;
  cursor: pointer;
  font-size: 0.9rem;
}

.collapse-toggle-btn:hover {
  background: #eee;
}

.sessions-panel-import-input {
  display: none;
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

.session-item-disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.session-item-disabled:hover {
  background: none;
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

.session-badge-row {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  min-width: 0;
  max-width: 100%;
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

/* "Label sessions" view's own marker (see session.has_annotations) —
   shown wherever this list is used, since it's just accurate information
   about the session, not something specific to reviewing it. */
.session-annotation-icon {
  flex-shrink: 0;
  font-size: 0.8rem;
  line-height: 1;
  cursor: help;
}

/* Teleported to <body>, position: fixed — see useFloatingTooltip.js. */
.session-annotation-tooltip-floating {
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
