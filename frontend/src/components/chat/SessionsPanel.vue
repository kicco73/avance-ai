<script setup>
import { ref } from 'vue'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'

// The sessions list content (header + rows), shared by every chat surface
// that lets a user pick a past/present session. Layout (the sliding wrap,
// its width, the drag divider) stays each parent's own concern — this
// component is just the list itself.
const props = defineProps({
  sessions: { type: Array, required: true },
  loading: { type: Boolean, default: false },
  currentSessionId: { type: [Number, String], default: null },
  // Which session (if any) a delete request is in flight for — disables
  // just that row's own delete button. Ignored when allowDelete is false.
  deletingSessionId: { type: [Number, String], default: null },
  allowCreate: { type: Boolean, default: true },
  // True when there's no project to start a session against (e.g. no
  // active project) — the button stays visible but inert, same pattern
  // as ProjectsMenu.vue's own grayed-out state.
  createDisabled: { type: Boolean, default: false },
  allowDelete: { type: Boolean, default: true },
  // When true, only an imported session is ever deletable, never a
  // native one.
  deleteImportedOnly: { type: Boolean, default: false },
  // Whether transcript import is offered — meaningful only for
  // review/labeling, not for a live chat.
  allowImport: { type: Boolean, default: false },
  // An imported session can never become the live conversation's active
  // session, so selecting one must be a no-op rather than handing
  // currentSessionId a session nothing downstream treats as live.
  restrictSelectionToNative: { type: Boolean, default: false },
  // The parent owns the actual width/layout collapse; this only owns its
  // own header toggle button and hiding its content while collapsed.
  collapsed: { type: Boolean, default: false },
  // True when the parent renders its own close control elsewhere (e.g.
  // ChatWindow.vue's, next to its ProjectsMenu row) instead of this
  // component's own header toggle button.
  hideCollapseToggle: { type: Boolean, default: false },
  // A footer button to download every session of this project as one
  // .json file.
  allowDownloadAll: { type: Boolean, default: false },
  downloadingAll: { type: Boolean, default: false }
})

const emit = defineEmits(['select', 'create', 'delete', 'import', 'download-all', 'update:collapsed'])

const importInput = ref(null)

function triggerImport() {
  importInput.value?.click()
}

function onImportFileChosen(event) {
  // Emitted as one array rather than one 'import' per file so the parent
  // can refresh the session list once after the whole batch settles.
  const files = Array.from(event.target.files ?? [])
  if (files.length) emit('import', files)
  // Reset so choosing the exact same file(s) again still fires 'change'.
  event.target.value = ''
}

function formatSessionTimestamp(iso) {
  if (!iso) return 'Timeline not available'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

function notSelectable(session) {
  return props.restrictSelectionToNative && session.type !== 'live'
}

function selectSession(session) {
  if (notSelectable(session)) return
  emit('select', session)
}

// The "has expert annotations" tag icon's tooltip — one shared instance
// for the whole list, since only one row can be hovered at a time.
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
    <div style="display: flex">

    <div v-if="!collapsed && allowImport" class="sessions-panel-header-actions">
      <button type="button" class="sessions-panel-icon-btn" title="Import transcript(s) — .txt or a 'Download all' .json export" @click="triggerImport">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
          <path d="M12 3l4 4h-3v6h-2V7H8l4-4zM5 19v-6h2v6h10v-6h2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z" />
        </svg>
      </button>
      <input ref="importInput" type="file" accept=".txt,text/plain,.json,application/json" multiple class="sessions-panel-import-input" @change="onImportFileChosen" />
    </div>
    <button
      v-if="!hideCollapseToggle"
      class="collapse-toggle-btn"
      :title="collapsed ? 'Expand sessions' : 'Collapse sessions'"
      @click="emit('update:collapsed', !collapsed)"
    >{{ collapsed ? '◂' : '✕' }}</button>
  </div>

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
        v-if="allowDelete && (!deleteImportedOnly || session.type === 'imported')"
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
  <button v-if="allowCreate" class="sessions-panel-add-btn" :disabled="createDisabled" @click="emit('create')">
    New session
  </button>
  <button v-if="allowDownloadAll" class="sessions-panel-download-btn" :disabled="downloadingAll" @click="emit('download-all')">
    {{ downloadingAll ? 'Downloading…' : 'Download all' }}
  </button>
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
  flex: 1;
  min-height: 0;
}

.sessions-panel-add-btn {
  flex-shrink: 0;
  margin: 0.5rem 0.9rem 0.9rem;
  padding: 0.5rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  font-size: 0.82rem;
  cursor: pointer;
}

.sessions-panel-add-btn:hover:not(:disabled) {
  background: #eef2f9;
}

.sessions-panel-add-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.sessions-panel-download-btn {
  flex-shrink: 0;
  width: 100%;
  padding: 0.5rem;
  border: none;
  border-top: 1px solid #ddd;
  border-radius: 0;
  background: #f7f8fa;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 600;
}

.sessions-panel-download-btn:hover:not(:disabled) {
  background: #eef2f9;
}

.sessions-panel-download-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
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

/* Shown wherever this list is used — accurate info about the session,
   not something specific to reviewing it. */
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
