<script setup>
// Label project's own session picker: a two-level tree, top level = the
// distinct users who own a session, second level = that user's own
// sessions — same caret/collapse interaction as
// project/edit/design/FileExplorer.vue, grouping modeled on
// TestsTree.vue's own `usersByUsername` branch. Unlike SessionsPanel.vue
// this is never a flat list and never offers create/delete — this view
// only ever reviews existing sessions.
//
// Node identifiers are plain strings prefixed by kind — `user:<username>`,
// `session:<id>` — the same convention TestsTree.vue uses.
import { computed, ref, watch } from 'vue'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'
import DocInfoButton from '../DocInfoButton.vue'

const props = defineProps({
  sessions: { type: Array, required: true },
  // Every registered user (see api.js's getUsers) — resolves a session's
  // `username` (a raw user id/email) into a display name when available;
  // falls back to the raw value otherwise.
  users: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  // Either `user:<username>` or `session:<id>`, or null before anything's
  // been picked.
  selectedNodeId: { type: String, default: null },
  allowImport: { type: Boolean, default: false },
  allowDownloadAll: { type: Boolean, default: false },
  downloadingAll: { type: Boolean, default: false },
  collapsed: { type: Boolean, default: false },
  hideCollapseToggle: { type: Boolean, default: false }
})

const emit = defineEmits(['select', 'import', 'download-all', 'update:collapsed'])

const importInput = ref(null)

function triggerImport() {
  importInput.value?.click()
}

function onImportFileChosen(event) {
  const files = Array.from(event.target.files ?? [])
  if (files.length) emit('import', files)
  event.target.value = ''
}

function formatSessionTimestamp(iso) {
  if (!iso) return 'Timeline not available'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

function displayNameFor(username) {
  const user = props.users.find((u) => u.id === username)
  return user?.name || user?.email || username
}

// One entry per distinct username, in first-seen order (sessions already
// arrive most-recent-first from the backend), each carrying its own
// sessions.
const usersByUsername = computed(() => {
  const order = []
  const map = new Map()
  for (const session of props.sessions) {
    if (!map.has(session.username)) {
      map.set(session.username, [])
      order.push(session.username)
    }
    map.get(session.username).push(session)
  }
  return order.map((username) => ({ username, label: displayNameFor(username), sessions: map.get(username) }))
})

const expanded = ref(new Set())
function toggleUser(username) {
  const next = new Set(expanded.value)
  if (next.has(username)) next.delete(username)
  else next.add(username)
  expanded.value = next
}

function onUserClick(username) {
  toggleUser(username)
  emit('select', `user:${username}`)
}

function onSessionClick(session) {
  emit('select', `session:${session.id}`)
}

// Reveals whichever user branch owns the node a selection elsewhere (e.g.
// picking the most recently active session on load) just landed on, even
// if that branch is currently collapsed.
watch(
  () => props.selectedNodeId,
  (nodeId) => {
    if (!nodeId) return
    let username = null
    if (nodeId.startsWith('user:')) username = nodeId.slice('user:'.length)
    else if (nodeId.startsWith('session:')) {
      const id = Number(nodeId.slice('session:'.length))
      username = props.sessions.find((s) => s.id === id)?.username ?? null
    }
    if (username != null && !expanded.value.has(username)) {
      const next = new Set(expanded.value)
      next.add(username)
      expanded.value = next
    }
  },
  { immediate: true }
)

// The "has expert annotations" tag icon's tooltip — one shared instance
// for the whole tree, since only one row can be hovered at a time.
const {
  visible: annotationTooltipVisible,
  style: annotationTooltipStyle,
  show: showAnnotationTooltip,
  hide: hideAnnotationTooltip
} = useFloatingTooltip()
</script>

<template>
  <div class="sessions-tree-header">
    <span v-if="!collapsed" class="sessions-tree-title">Sessions</span>
    <div style="display: flex">
      <div v-if="!collapsed && allowImport" class="sessions-tree-header-actions">
        <button type="button" class="sessions-tree-icon-btn" title="Import transcript(s) — .txt or a 'Download all' .json export" @click="triggerImport">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M12 3l4 4h-3v6h-2V7H8l4-4zM5 19v-6h2v6h10v-6h2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z" />
          </svg>
        </button>
        <input ref="importInput" type="file" accept=".txt,text/plain,.json,application/json" multiple class="sessions-tree-import-input" @change="onImportFileChosen" />
      </div>
      <button
        v-if="!hideCollapseToggle"
        class="collapse-toggle-btn"
        :title="collapsed ? 'Expand sessions' : 'Collapse sessions'"
        @click="emit('update:collapsed', !collapsed)"
      >{{ collapsed ? '▸' : '◂' }}</button>
    </div>
  </div>

  <template v-if="!collapsed">
    <p v-if="loading" class="sessions-tree-status">Loading…</p>
    <p v-else-if="!usersByUsername.length" class="sessions-tree-status">No sessions yet.</p>

    <ul v-else class="sessions-tree">
      <li v-for="user in usersByUsername" :key="user.username" class="sessions-tree-branch">
        <div class="sessions-tree-node-row">
          <button
            class="sessions-tree-caret"
            :class="{ 'sessions-tree-caret-open': expanded.has(user.username) }"
            title="Toggle"
            @click="toggleUser(user.username)"
          >▸</button>
          <button
            type="button"
            class="sessions-tree-item"
            :class="{ 'sessions-tree-item-active': selectedNodeId === `user:${user.username}` }"
            :title="user.label"
            @click="onUserClick(user.username)"
          >
            {{ user.label }}
          </button>
        </div>

        <div class="sessions-tree-children-wrap" :class="{ 'sessions-tree-children-wrap-open': expanded.has(user.username) }">
          <ul class="sessions-tree-children">
            <li v-for="session in user.sessions" :key="session.id" class="sessions-tree-session-row">
              <button
                type="button"
                class="sessions-tree-session-item"
                :class="{ 'sessions-tree-session-item-active': selectedNodeId === `session:${session.id}` }"
                @click="onSessionClick(session)"
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
            </li>
          </ul>
        </div>
      </li>
    </ul>

    <div v-if="allowDownloadAll" class="sessions-tree-download-row">
      <button class="sessions-tree-download-btn" :disabled="downloadingAll" @click="emit('download-all')">
        {{ downloadingAll ? 'Downloading…' : 'Download all' }}
      </button>
      <DocInfoButton doc-name="session-specs" title="Session export format" />
    </div>
  </template>

  <Teleport to="body">
    <span v-if="annotationTooltipVisible" class="session-annotation-tooltip-floating" :style="annotationTooltipStyle">
      Has expert annotations
    </span>
  </Teleport>
</template>

<style scoped>
.sessions-tree-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.6rem 0.9rem;
  border-bottom: 1px solid #ddd;
}

.sessions-tree-title {
  font-size: 0.8rem;
  font-weight: 600;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.sessions-tree-header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.sessions-tree-icon-btn {
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

.sessions-tree-icon-btn:hover {
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

.sessions-tree-import-input {
  display: none;
}

.sessions-tree-status {
  margin: 0;
  padding: 0.75rem 0.9rem;
  font-size: 0.85rem;
  color: #666;
}

.sessions-tree {
  list-style: none;
  margin: 0;
  padding: 0.3rem;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}

.sessions-tree-branch + .sessions-tree-branch {
  margin-top: 0.2rem;
}

.sessions-tree-node-row {
  display: flex;
  align-items: center;
  gap: 0.1rem;
}

.sessions-tree-caret {
  flex-shrink: 0;
  width: 1.2rem;
  height: 1.6rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 0.7rem;
  color: #777;
  padding: 0;
  transform: rotate(0deg);
  transition: transform 0.18s ease;
}

.sessions-tree-caret-open {
  transform: rotate(90deg);
}

.sessions-tree-item {
  flex: 1;
  min-width: 0;
  display: block;
  text-align: left;
  padding: 0.4rem 0.5rem;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  color: #333;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.sessions-tree-item:hover {
  background: #f0f4fa;
}

.sessions-tree-item-active {
  background: #e4ecf9;
  color: #2c4d7a;
  font-weight: 600;
}

.sessions-tree-children-wrap {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 0.18s ease;
}

.sessions-tree-children-wrap-open {
  grid-template-rows: 1fr;
}

.sessions-tree-children {
  list-style: none;
  margin: 0;
  padding: 0 0 0 1.2rem;
  overflow: hidden;
  min-height: 0;
}

.sessions-tree-session-row {
  display: flex;
  align-items: center;
  gap: 0.2rem;
  padding: 0 0.2rem;
}

.sessions-tree-session-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.3rem;
  flex: 1;
  min-width: 0;
  text-align: left;
  padding: 0.45rem 0.5rem;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
}

.sessions-tree-session-item:hover {
  background: #eef2f8;
}

.sessions-tree-session-item-active {
  background: #e3ebf7;
}

.sessions-tree-download-row {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin: 0.5rem 0.9rem 0.9rem;
}

.sessions-tree-download-btn {
  flex: 1;
  min-width: 0;
  padding: 0.5rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.82rem;
}

.sessions-tree-download-btn:hover:not(:disabled) {
  background: #eef2f9;
}

.sessions-tree-download-btn:disabled {
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
