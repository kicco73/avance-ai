<script setup>
// Two-level tree under one root: "Sessions" (a leaf per annotated session)
// and "States" (a leaf per state key). Root and the two branch nodes are
// activatable too — root/sessions-branch launches the whole-project
// replay, states-branch launches every state's own test — and carry that
// scope's own aggregate status, same as any leaf. ProjectTestPanel.vue
// owns all data fetching/launching/polling — this component only renders and emits.
//
// Node identifiers are plain strings prefixed by kind — 'root',
// 'sessions-branch', 'states-branch', `session:<id>`, `state:<key>` — used
// directly as both the emitted identifier and the key into `statuses` below.
import { computed, ref } from 'vue'
import TestNodeButton from './TestNodeButton.vue'

const props = defineProps({
  projectName: { type: String, required: true },
  // Full session list (see chatStore.js's sessions, fetched with
  // include_imported=true upstream) — filtered here to has_annotations,
  // the only ones the "Sessions" branch ever shows.
  sessions: { type: Array, required: true },
  // Every real state key of the project's current draft automaton (see
  // api.js's getProjectStates).
  states: { type: Array, required: true },
  // { [nodeId]: 'idle'|'running'|'ok'|'warning'|'fail' } — idle is the
  // implicit default for any id missing from this map.
  statuses: { type: Object, default: () => ({}) },
  progresses: { type: Object, default: () => ({}) },
  selectedNodeId: { type: String, default: null }
})

const emit = defineEmits(['select', 'activate'])

const expanded = ref({ root: true, sessions: true, states: true, users: true })
const expandedUsers = ref({})

function isExpanded(key) {
  return expanded.value[key] !== false
}
function toggleExpanded(key) {
  expanded.value[key] = !isExpanded(key)
}
function isUserExpanded(username) {
  return expandedUsers.value[username] !== false
}
function toggleUserExpanded(username) {
  expandedUsers.value[username] = !isUserExpanded(username)
}

const annotatedSessions = computed(() => props.sessions.filter((s) => s.has_annotations))

// One entry per distinct username among annotated sessions, each carrying
// its own annotated sessions — the "Users" branch's own two-level shape.
const usersByUsername = computed(() => {
  const grouped = new Map()
  for (const session of annotatedSessions.value) {
    if (!grouped.has(session.username)) grouped.set(session.username, [])
    grouped.get(session.username).push(session)
  }
  return [...grouped.entries()].map(([username, userSessions]) => ({ username, sessions: userSessions }))
})

function statusFor(nodeId) {
  return props.statuses[nodeId] ?? 'idle'
}

function progressFor(nodeId) {
  return props.progresses[nodeId] ?? null
}

const FINAL_SESSION_REASON = 'Result is final for the current project and annotation state'
function sessionButtonDisabled(session) {
  const status = statusFor(`session:${session.id}`)
  return status === 'ok' || status === 'warning'
}

function sessionLabel(session) {
  return session.title || session.end_state || `Session ${session.id}`
}

function formatSessionTimestamp(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

const flatNodeIds = computed(() => {
  const ids = ['root']
  if (!isExpanded('root')) return ids
  ids.push('sessions-branch')
  if (isExpanded('sessions')) ids.push(...annotatedSessions.value.map((s) => `session:${s.id}`))
  ids.push('states-branch')
  if (isExpanded('states')) ids.push(...props.states.map((key) => `state:${key}`))
  ids.push('users-branch')
  if (isExpanded('users')) {
    for (const user of usersByUsername.value) {
      ids.push(`user:${user.username}`)
      if (isUserExpanded(user.username)) ids.push(...user.sessions.map((s) => `session:${s.id}`))
    }
  }
  return ids
})

const treeRef = ref(null)

function moveSelection(delta) {
  const ids = flatNodeIds.value
  if (!ids.length) return
  const currentIndex = ids.indexOf(props.selectedNodeId)
  const nextIndex = Math.max(0, Math.min(ids.length - 1, currentIndex + delta))
  const nextId = ids[nextIndex]
  emit('select', nextId)
  // Keep keyboard focus (and the scroll position) on the row that's now
  // selected, same as clicking it would — querySelector over refs since
  // every row already carries its own nodeId as a data attribute.
  treeRef.value?.querySelector(`[data-node-id="${CSS.escape(nextId)}"]`)?.focus()
}
</script>

<template>
  <ul
    ref="treeRef"
    class="tests-tree"
    tabindex="0"
    @keydown.up.prevent="moveSelection(-1)"
    @keydown.down.prevent="moveSelection(1)"
  >
    <li class="tests-tree-node">
      <div class="tests-tree-node-row">
        <button class="tests-tree-caret" :class="{ 'tests-tree-caret-open': isExpanded('root') }" title="Toggle" @click="toggleExpanded('root')">▸</button>
        <div class="tests-tree-item">
          <TestNodeButton :status="statusFor('root')" :progress="progressFor('root')" @activate="emit('activate', 'root')" />
          <button
            type="button"
            class="tests-tree-row"
            data-node-id="root"
            :class="{ 'tests-tree-row-selected': selectedNodeId === 'root' }"
            @click="emit('select', 'root')"
          >
            <span class="tests-tree-label">{{ projectName }}</span>
          </button>
        </div>
      </div>

      <div class="tests-tree-children-wrap" :class="{ 'tests-tree-children-wrap-open': isExpanded('root') }">
      <ul class="tests-tree-children">
        <li class="tests-tree-node">
          <div class="tests-tree-node-row">
            <button class="tests-tree-caret" :class="{ 'tests-tree-caret-open': isExpanded('sessions') }" title="Toggle" @click="toggleExpanded('sessions')">▸</button>
            <div class="tests-tree-item">
              <TestNodeButton :status="statusFor('sessions-branch')" :progress="progressFor('sessions-branch')" @activate="emit('activate', 'sessions-branch')" />
              <button
                type="button"
                class="tests-tree-row"
                data-node-id="sessions-branch"
                :class="{ 'tests-tree-row-selected': selectedNodeId === 'sessions-branch' }"
                @click="emit('select', 'sessions-branch')"
              >
                <span class="tests-tree-label">Sessions</span>
              </button>
            </div>
          </div>

          <div class="tests-tree-children-wrap" :class="{ 'tests-tree-children-wrap-open': isExpanded('sessions') }">
          <ul class="tests-tree-children tests-tree-children-leaf">
            <li v-if="!annotatedSessions.length" class="tests-tree-empty">No annotated sessions yet.</li>
            <li v-for="session in annotatedSessions" :key="session.id" class="tests-tree-node">
              <div class="tests-tree-item">
                <TestNodeButton
                  :status="statusFor(`session:${session.id}`)"
                  :progress="progressFor(`session:${session.id}`)"
                  :disabled="sessionButtonDisabled(session)"
                  :disabled-reason="FINAL_SESSION_REASON"
                  @activate="emit('activate', `session:${session.id}`)"
                />
                <button
                  type="button"
                  class="tests-tree-row"
                  :data-node-id="`session:${session.id}`"
                  :class="{ 'tests-tree-row-selected': selectedNodeId === `session:${session.id}` }"
                  @click="emit('select', `session:${session.id}`)"
                >
                  <span class="tests-tree-label">{{ sessionLabel(session) }}</span>
                  <span class="tests-tree-sublabel">{{ formatSessionTimestamp(session.datetime_start) }}</span>
                </button>
              </div>
            </li>
          </ul>
          </div>
        </li>

        <li class="tests-tree-node">
          <div class="tests-tree-node-row">
            <button class="tests-tree-caret" :class="{ 'tests-tree-caret-open': isExpanded('states') }" title="Toggle" @click="toggleExpanded('states')">▸</button>
            <div class="tests-tree-item">
              <TestNodeButton :status="statusFor('states-branch')" :progress="progressFor('states-branch')" @activate="emit('activate', 'states-branch')" />
              <button
                type="button"
                class="tests-tree-row"
                data-node-id="states-branch"
                :class="{ 'tests-tree-row-selected': selectedNodeId === 'states-branch' }"
                @click="emit('select', 'states-branch')"
              >
                <span class="tests-tree-label">States</span>
              </button>
            </div>
          </div>

          <div class="tests-tree-children-wrap" :class="{ 'tests-tree-children-wrap-open': isExpanded('states') }">
          <ul class="tests-tree-children tests-tree-children-leaf">
            <li v-if="!states.length" class="tests-tree-empty">No states yet.</li>
            <li v-for="stateKey in states" :key="stateKey" class="tests-tree-node">
              <div class="tests-tree-item">
                <TestNodeButton
                  :status="statusFor(`state:${stateKey}`)"
                  :progress="progressFor(`state:${stateKey}`)"
                  @activate="emit('activate', `state:${stateKey}`)"
                />
                <button
                  type="button"
                  class="tests-tree-row"
                  :data-node-id="`state:${stateKey}`"
                  :class="{ 'tests-tree-row-selected': selectedNodeId === `state:${stateKey}` }"
                  @click="emit('select', `state:${stateKey}`)"
                >
                  <span class="tests-tree-label">{{ stateKey }}</span>
                </button>
              </div>
            </li>
          </ul>
          </div>
        </li>

        <li class="tests-tree-node">
          <div class="tests-tree-node-row">
            <button class="tests-tree-caret" :class="{ 'tests-tree-caret-open': isExpanded('users') }" title="Toggle" @click="toggleExpanded('users')">▸</button>
            <div class="tests-tree-item">
              <TestNodeButton :status="statusFor('users-branch')" :progress="progressFor('users-branch')" @activate="emit('activate', 'users-branch')" />
              <button
                type="button"
                class="tests-tree-row"
                data-node-id="users-branch"
                :class="{ 'tests-tree-row-selected': selectedNodeId === 'users-branch' }"
                @click="emit('select', 'users-branch')"
              >
                <span class="tests-tree-label">Users</span>
              </button>
            </div>
          </div>

          <div class="tests-tree-children-wrap" :class="{ 'tests-tree-children-wrap-open': isExpanded('users') }">
          <ul class="tests-tree-children">
            <li v-if="!usersByUsername.length" class="tests-tree-empty">No annotated sessions yet.</li>
            <li v-for="user in usersByUsername" :key="user.username" class="tests-tree-node">
              <div class="tests-tree-node-row">
                <button class="tests-tree-caret" :class="{ 'tests-tree-caret-open': isUserExpanded(user.username) }" title="Toggle" @click="toggleUserExpanded(user.username)">▸</button>
                <div class="tests-tree-item">
                  <TestNodeButton
                    :status="statusFor(`user:${user.username}`)"
                    :progress="progressFor(`user:${user.username}`)"
                    @activate="emit('activate', `user:${user.username}`)"
                  />
                  <button
                    type="button"
                    class="tests-tree-row"
                    :data-node-id="`user:${user.username}`"
                    :class="{ 'tests-tree-row-selected': selectedNodeId === `user:${user.username}` }"
                    @click="emit('select', `user:${user.username}`)"
                  >
                    <span class="tests-tree-label">{{ user.username }}</span>
                  </button>
                </div>
              </div>

              <div class="tests-tree-children-wrap" :class="{ 'tests-tree-children-wrap-open': isUserExpanded(user.username) }">
              <ul class="tests-tree-children tests-tree-children-leaf">
                <li v-for="session in user.sessions" :key="session.id" class="tests-tree-node">
                  <div class="tests-tree-item">
                    <TestNodeButton
                      :status="statusFor(`session:${session.id}`)"
                      :progress="progressFor(`session:${session.id}`)"
                      :disabled="sessionButtonDisabled(session)"
                      :disabled-reason="FINAL_SESSION_REASON"
                      @activate="emit('activate', `session:${session.id}`)"
                    />
                    <button
                      type="button"
                      class="tests-tree-row"
                      :data-node-id="`session:${session.id}`"
                      :class="{ 'tests-tree-row-selected': selectedNodeId === `session:${session.id}` }"
                      @click="emit('select', `session:${session.id}`)"
                    >
                      <span class="tests-tree-label">{{ sessionLabel(session) }}</span>
                      <span class="tests-tree-sublabel">{{ formatSessionTimestamp(session.datetime_start) }}</span>
                    </button>
                  </div>
                </li>
              </ul>
              </div>
            </li>
          </ul>
          </div>
        </li>
      </ul>
      </div>
    </li>
  </ul>
</template>

<style scoped>
.tests-tree {
  list-style: none;
  margin: 0;
  padding: 0.4rem;
  overflow-y: auto;
}

.tests-tree:focus {
  outline: none;
}

.tests-tree-row:focus-visible {
  outline: 2px solid #4a6fa5;
  outline-offset: -2px;
}

.tests-tree-node-row {
  display: flex;
  align-items: center;
  gap: 0.1rem;
}

.tests-tree-caret {
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

.tests-tree-caret-open {
  transform: rotate(90deg);
}

.tests-tree-children-wrap {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 0.18s ease;
}

.tests-tree-children-wrap-open {
  grid-template-rows: 1fr;
}

.tests-tree-children {
  list-style: none;
  margin: 0;
  padding-left: 1.1rem;
  overflow: hidden;
  min-height: 0;
}

.tests-tree-children-leaf {
  padding-left: calc(1.7rem + 8px);
}

.tests-tree-node {
  margin: 0.1rem 0;
}

.tests-tree-item {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.tests-tree-row {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.1rem;
  text-align: left;
  padding: 0.35rem 0.5rem;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
}

.tests-tree-row:hover {
  background: #eef2f8;
}

.tests-tree-row-selected {
  background: #e3ebf7;
}

.tests-tree-label {
  font-size: 0.85rem;
  color: #222;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.tests-tree-sublabel {
  font-size: 0.72rem;
  color: #777;
}

.tests-tree-empty {
  padding: 0.35rem 0.5rem;
  font-size: 0.78rem;
  color: #888;
}
</style>
