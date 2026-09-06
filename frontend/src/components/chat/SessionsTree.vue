<script setup>
import { computed, ref, watch } from 'vue'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'
import { useSessionSelection } from '../../composables/useSessionSelection.js'
import { useSessionDragAndDrop } from '../../composables/useSessionDragAndDrop.js'
import DocInfoButton from '../DocInfoButton.vue'
import SessionsTreeHeader from './SessionsTreeHeader.vue'

const props = defineProps({
  sessions: { type: Array, required: true },
  users: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  selectedNodeId: { type: String, default: null },
  allowImport: { type: Boolean, default: false },
  importing: { type: Boolean, default: false },
  importProgress: { type: Number, default: null },
  allowDownloadAll: { type: Boolean, default: false },
  downloadingAll: { type: Boolean, default: false },
  allowDeleteAllImported: { type: Boolean, default: false },
  deletingAllImported: { type: Boolean, default: false },
  collapsed: { type: Boolean, default: false },
  hideCollapseToggle: { type: Boolean, default: false }
})

const emit = defineEmits([
  'select', 'import', 'download-all', 'delete-all-imported', 'update:collapsed', 'move-sessions',
  'delete-test-user', 'delete-user-sessions'
])

const TEST_USER_PREFIX = 'Test user '

function isTestUserBranch(username) {
  return username.startsWith(TEST_USER_PREFIX)
}

function testUserSeqOf(username) {
  return Number(username.slice(TEST_USER_PREFIX.length))
}

const hasImportedSessions = computed(() => props.sessions.some((s) => s.type === 'imported'))

const activeTab = ref('live')
const filteredSessions = computed(() => props.sessions.filter((s) => s.type === activeTab.value))

// Reassignment is imported-only backend-side: a live session's username is its owner's identity.
const canDragAndDrop = computed(() => activeTab.value === 'imported')

function formatSessionTimestamp(iso) {
  if (!iso) return 'Timeline not available'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

function displayNameFor(username) {
  if (isTestUserBranch(username)) return `Test User ${testUserSeqOf(username)}`
  const user = props.users.find((u) => u.id === username)
  return user?.name || user?.email || username
}

const CHANNEL_LABELS = { 'whatsapp-chat': 'WhatsApp' }

function channelLabel(session) {
  return session.type === 'live' ? CHANNEL_LABELS[session.channel] : null
}

const branchOrder = ref([])
watch(
  filteredSessions,
  (list) => {
    const current = new Set(list.map((s) => s.username))
    const next = branchOrder.value.filter((username) => current.has(username))
    for (const session of list) {
      if (!next.includes(session.username)) next.push(session.username)
    }
    branchOrder.value = next
  },
  { immediate: true }
)

const usersByUsername = computed(() => {
  const map = new Map()
  for (const session of filteredSessions.value) {
    if (!map.has(session.username)) map.set(session.username, [])
    map.get(session.username).push(session)
  }
  return branchOrder.value
    .filter((username) => map.has(username))
    .map((username) => ({ username, label: displayNameFor(username), sessions: map.get(username) }))
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

const selection = useSessionSelection(canDragAndDrop, emit)
const { selectedSessionIds, onSessionRowClick } = selection
const {
  isDragOver, onSessionDragStart, onSessionDragEnd, onBranchDragEnter, onBranchDragLeave, onBranchDragOver, onBranchDrop
} = useSessionDragAndDrop(canDragAndDrop, selection, emit)

function isDeletableBranch(user) {
  return user.sessions.every((s) => s.type !== 'live')
}

function onDeleteBranchClick(user) {
  if (isTestUserBranch(user.username)) emit('delete-test-user', { testUserSeq: testUserSeqOf(user.username) })
  else emit('delete-user-sessions', { username: user.username })
}

watch(
  () => props.selectedNodeId,
  (nodeId) => {
    if (!nodeId) return
    let username = null
    if (nodeId.startsWith('user:')) username = nodeId.slice('user:'.length)
    else if (nodeId.startsWith('session:')) {
      const id = Number(nodeId.slice('session:'.length))
      const session = props.sessions.find((s) => s.id === id)
      username = session?.username ?? null
      if (session) activeTab.value = session.type
    }
    if (username != null && !expanded.value.has(username)) {
      const next = new Set(expanded.value)
      next.add(username)
      expanded.value = next
    }
  },
  { immediate: true }
)

const {
  visible: annotationTooltipVisible,
  style: annotationTooltipStyle,
  show: showAnnotationTooltip,
  hide: hideAnnotationTooltip
} = useFloatingTooltip()
</script>

<template>
  <SessionsTreeHeader
    :collapsed="collapsed"
    :hide-collapse-toggle="hideCollapseToggle"
    :allow-import="allowImport"
    :importing="importing"
    :import-progress="importProgress"
    :show-delete-all-imported="allowDeleteAllImported && activeTab === 'imported'"
    :delete-all-disabled="!hasImportedSessions"
    :deleting-all-imported="deletingAllImported"
    @import="emit('import', $event)"
    @delete-all-imported="emit('delete-all-imported')"
    @update:collapsed="emit('update:collapsed', $event)"
  />

  <template v-if="!collapsed">
    <div class="sessions-tree-tabs">
      <button
        type="button"
        class="sessions-tree-tab"
        :class="{ 'sessions-tree-tab-active': activeTab === 'live' }"
        @click="activeTab = 'live'"
      >Live</button>
      <button
        type="button"
        class="sessions-tree-tab"
        :class="{ 'sessions-tree-tab-active': activeTab === 'imported' }"
        @click="activeTab = 'imported'"
      >Imported</button>
    </div>

    <p v-if="loading" class="sessions-tree-status">Loading…</p>
    <p v-else-if="!usersByUsername.length" class="sessions-tree-status">No sessions yet.</p>

    <TransitionGroup v-else tag="ul" name="branch-fade" class="sessions-tree">
      <li
        v-for="user in usersByUsername"
        :key="user.username"
        class="sessions-tree-branch"
        :class="{ 'sessions-tree-branch-drag-over': isDragOver(user.username) }"
        @dragenter="onBranchDragEnter(user, $event)"
        @dragover="onBranchDragOver(user, $event)"
        @dragleave="onBranchDragLeave(user)"
        @drop="onBranchDrop(user, $event)"
      >
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
          <button
            v-if="isDeletableBranch(user)"
            type="button"
            class="sessions-tree-delete-btn"
            :title="isTestUserBranch(user.username) ? 'Delete this test user' : 'Delete all sessions from this user'"
            @click.stop="onDeleteBranchClick(user)"
          >&times;</button>
        </div>

        <div class="sessions-tree-children-wrap" :class="{ 'sessions-tree-children-wrap-open': expanded.has(user.username) }">
          <TransitionGroup tag="ul" name="session-move" class="sessions-tree-children">
            <li v-for="session in user.sessions" :key="session.id" class="sessions-tree-session-row">
              <button
                type="button"
                class="sessions-tree-session-item"
                :class="{
                  'sessions-tree-session-item-active': selectedNodeId === `session:${session.id}`,
                  'sessions-tree-session-item-selected': canDragAndDrop && selectedSessionIds.has(session.id)
                }"
                :disabled="session.unsupported_revision"
                :title="session.unsupported_revision ? `This session is pinned to revision ${session.project_revision}, which this version of Avance can no longer run.` : undefined"
                :draggable="canDragAndDrop && !session.unsupported_revision"
                @click="onSessionRowClick(session, user, $event)"
                @dragstart="onSessionDragStart(session, user, $event)"
                @dragend="onSessionDragEnd"
              >
                <span class="sessions-tree-session-content">
                  <span class="session-badge-row">
                    <span
                      class="session-badge"
                      :class="{ 'session-badge-inactive': !session.active, 'session-badge-unsupported': session.unsupported_revision }"
                    >
                      {{ session.title || session.end_state }}
                    </span>
                    <span v-if="session.unsupported_revision" class="session-unsupported-label">unsupported revision</span>
                    <span v-if="channelLabel(session)" class="session-channel-label">{{ channelLabel(session) }}</span>
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
                </span>
                <span v-if="canDragAndDrop" class="sessions-tree-drag-handle" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor">
                    <circle cx="8" cy="6" r="1.6" />
                    <circle cx="8" cy="12" r="1.6" />
                    <circle cx="8" cy="18" r="1.6" />
                    <circle cx="16" cy="6" r="1.6" />
                    <circle cx="16" cy="12" r="1.6" />
                    <circle cx="16" cy="18" r="1.6" />
                  </svg>
                </span>
              </button>
            </li>
          </TransitionGroup>
        </div>
      </li>
    </TransitionGroup>

    <div v-if="allowDownloadAll" class="sessions-tree-download-row">
      <button class="sessions-tree-download-btn" :disabled="downloadingAll" @click="emit('download-all', activeTab)">
        {{ downloadingAll ? 'Downloading…' : `Download all ${activeTab}` }}
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
.sessions-tree-status {
  margin: 0;
  padding: 0.75rem 0.9rem;
  font-size: 0.85rem;
  color: #666;
}

.sessions-tree-tabs {
  display: flex;
  gap: 0.25rem;
  padding: 0.5rem 0.9rem 0;
  border-bottom: 1px solid #ddd;
}

.sessions-tree-tab {
  flex: 1;
  text-align: center;
  padding: 0.45rem 0.9rem;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: none;
  cursor: pointer;
  font-size: 0.82rem;
  color: #666;
}

.sessions-tree-tab:hover {
  color: #333;
}

.sessions-tree-tab-active {
  color: #2c4d7a;
  font-weight: 600;
  border-bottom-color: #4a6fa5;
}

.sessions-tree {
  position: relative;
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

.sessions-tree-branch-drag-over {
  outline: 2px dashed #4a6fa5;
  outline-offset: 2px;
  background: #eef2fb;
  border-radius: 8px;
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

.sessions-tree-delete-btn {
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

.sessions-tree-delete-btn:hover {
  background: #fdecea;
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
  position: relative;
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
  flex-direction: row;
  align-items: center;
  gap: 0.4rem;
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

.sessions-tree-session-item:disabled {
  cursor: not-allowed;
}

.sessions-tree-session-item:disabled:hover {
  background: none;
}

.sessions-tree-drag-handle {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  color: #aaa;
  cursor: grab;
}

.sessions-tree-session-item:hover .sessions-tree-drag-handle {
  color: #4a6fa5;
}

.sessions-tree-session-content {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.3rem;
  flex: 1;
  min-width: 0;
}

.sessions-tree-session-item-active {
  background: #e3ebf7;
}

.sessions-tree-session-item-selected {
  box-shadow: inset 0 0 0 1px #4a6fa5;
}

.session-move-enter-active,
.session-move-leave-active {
  transition: opacity 0.22s ease;
}

.session-move-move {
  transition: transform 0.22s ease;
}

.session-move-enter-from,
.session-move-leave-to {
  opacity: 0;
}

.session-move-leave-active {
  position: absolute;
  width: 100%;
}

.branch-fade-enter-active,
.branch-fade-leave-active {
  transition: opacity 0.22s ease;
}

.branch-fade-move {
  transition: transform 0.22s ease;
}

.branch-fade-enter-from,
.branch-fade-leave-to {
  opacity: 0;
}

.branch-fade-leave-active {
  position: absolute;
  width: 100%;
}

.sessions-tree-download-row {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin: auto 0.9rem 0.9rem;
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

.session-badge-unsupported {
  background: #999;
  opacity: 0.6;
}

.session-unsupported-label {
  flex-shrink: 0;
  padding: 0.1rem 0.4rem;
  border-radius: 999px;
  background: #fdecea;
  color: #c0392b;
  font-size: 0.65rem;
  font-weight: 600;
}

.session-channel-label {
  flex-shrink: 0;
  font-size: 0.7rem;
  color: #666;
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
