<script setup>
// Two-level tree under one root: "Sessioni" (a leaf per annotated
// session) and "Stati" (a leaf per real state key of the project's
// automaton) — see ProjectAutoPanel.vue, which owns all the actual data
// fetching/launching/polling. This component only renders and emits;
// it never calls an API itself.
//
// Node identifiers are plain strings, prefixed by kind — 'root',
// 'sessions-branch', 'states-branch', `session:<id>`, `state:<key>` —
// simple enough to use directly as both the emitted identifier and the
// key into `statuses` below, and to tell a session node from a state one
// at a glance.
import { computed } from 'vue'
import TestNodeButton from './TestNodeButton.vue'

const props = defineProps({
  projectName: { type: String, required: true },
  // Full session list (see chatStore.js's sessions, fetched with
  // include_imported=true upstream) — filtered here to has_annotations,
  // the only ones the "Sessioni" branch ever shows.
  sessions: { type: Array, required: true },
  // Every real state key of the project's current draft automaton (see
  // api.js's getProjectStates).
  states: { type: Array, required: true },
  // { [nodeId]: 'idle'|'running'|'ok'|'warning'|'fail' } — idle is the
  // implicit default for any id missing from this map.
  statuses: { type: Object, default: () => ({}) },
  selectedNodeId: { type: String, default: null }
})

const emit = defineEmits(['select', 'activate'])

const annotatedSessions = computed(() => props.sessions.filter((s) => s.has_annotations))

function statusFor(nodeId) {
  return props.statuses[nodeId] ?? 'idle'
}

function sessionLabel(session) {
  return session.title || session.end_state || `Session ${session.id}`
}

function formatSessionTimestamp(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}
</script>

<template>
  <ul class="tests-tree">
    <li class="tests-tree-node">
      <div class="tests-tree-item">
        <button
          type="button"
          class="tests-tree-row"
          :class="{ 'tests-tree-row-selected': selectedNodeId === 'root' }"
          @click="emit('select', 'root')"
        >
          <span class="tests-tree-label">{{ projectName }}</span>
        </button>
        <TestNodeButton :status="statusFor('root')" :disabled="true" />
      </div>

      <ul class="tests-tree-children">
        <li class="tests-tree-node">
          <div class="tests-tree-item">
            <button
              type="button"
              class="tests-tree-row"
              :class="{ 'tests-tree-row-selected': selectedNodeId === 'sessions-branch' }"
              @click="emit('select', 'sessions-branch')"
            >
              <span class="tests-tree-label">Sessioni</span>
            </button>
            <TestNodeButton :status="statusFor('sessions-branch')" :disabled="true" />
          </div>

          <ul class="tests-tree-children">
            <li v-if="!annotatedSessions.length" class="tests-tree-empty">No annotated sessions yet.</li>
            <li v-for="session in annotatedSessions" :key="session.id" class="tests-tree-node">
              <div class="tests-tree-item">
                <button
                  type="button"
                  class="tests-tree-row"
                  :class="{ 'tests-tree-row-selected': selectedNodeId === `session:${session.id}` }"
                  @click="emit('select', `session:${session.id}`)"
                >
                  <span class="tests-tree-label">{{ sessionLabel(session) }}</span>
                  <span class="tests-tree-sublabel">{{ formatSessionTimestamp(session.datetime_start) }}</span>
                </button>
                <TestNodeButton
                  :status="statusFor(`session:${session.id}`)"
                  @activate="emit('activate', `session:${session.id}`)"
                />
              </div>
            </li>
          </ul>
        </li>

        <li class="tests-tree-node">
          <div class="tests-tree-item">
            <button
              type="button"
              class="tests-tree-row"
              :class="{ 'tests-tree-row-selected': selectedNodeId === 'states-branch' }"
              @click="emit('select', 'states-branch')"
            >
              <span class="tests-tree-label">Stati</span>
            </button>
            <TestNodeButton :status="statusFor('states-branch')" :disabled="true" />
          </div>

          <ul class="tests-tree-children">
            <li v-if="!states.length" class="tests-tree-empty">No states yet.</li>
            <li v-for="stateKey in states" :key="stateKey" class="tests-tree-node">
              <div class="tests-tree-item">
                <button
                  type="button"
                  class="tests-tree-row"
                  :class="{ 'tests-tree-row-selected': selectedNodeId === `state:${stateKey}` }"
                  @click="emit('select', `state:${stateKey}`)"
                >
                  <span class="tests-tree-label">{{ stateKey }}</span>
                </button>
                <TestNodeButton
                  :status="statusFor(`state:${stateKey}`)"
                  @activate="emit('activate', `state:${stateKey}`)"
                />
              </div>
            </li>
          </ul>
        </li>
      </ul>
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

.tests-tree-children {
  list-style: none;
  margin: 0;
  padding-left: 1.1rem;
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
