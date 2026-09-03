<script setup>
// Detail card for one scheduled Task row (db/tasks.py) — same badge/
// title/open-closed convention as ServicesProviderCard.vue's own
// read-only provider card, with a "Task" badge and a status flag
// instead of a provider's mode/language flags.
import { computed, ref } from 'vue'
import { renderMarkdown } from '../../markdown.js'

const props = defineProps({
  task: { type: Object, required: true } // {id, key, type, username, project_id, run_at, ui_label, ui_description, status, error, created_at, dispatched_at, settled_at}
})

const open = ref(false)

function toggle() {
  open.value = !open.value
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString() : null
}

// Everything but ui_label/ui_description (title/description above) and
// status (its own flag badge) — dates formatted for display, blank
// fields (dispatched/settled/error, unset until the task gets there)
// dropped rather than shown empty.
const fields = computed(() => {
  const entries = [
    ['Run at', formatDate(props.task.run_at)],
    ['Project', props.task.project_id],
    ['Username', props.task.username],
    ['Type', props.task.type],
    ['Created', formatDate(props.task.created_at)],
    ['Dispatched', formatDate(props.task.dispatched_at)],
    ['Settled', formatDate(props.task.settled_at)],
    ['Error', props.task.error]
  ]
  return entries.filter(([, value]) => value != null && value !== '')
})
</script>

<template>
  <div class="inspector-detail-card" :class="{ 'inspector-detail-card-open': open }" @click="toggle">
    <div class="inspector-detail-header">
      <div class="inspector-detail-header-top">
        <span class="inspector-detail-badge inspector-detail-badge-task">Task</span>
        <span class="inspector-detail-title">{{ task.ui_label }}</span>
      </div>
      <div class="inspector-detail-badges">
        <span class="inspector-detail-badge" :class="`task-status-${task.status}`">{{ task.status }}</span>
      </div>
    </div>
    <div class="inspector-detail-body">
      <Transition name="crossfade" mode="out-in">
        <div v-if="open" key="open">
          <div v-for="[label, value] in fields" :key="label" class="services-field">
            <label class="services-field-label">{{ label }}</label>
            <input class="services-field-input" type="text" :value="value" disabled />
          </div>
          <div
            v-if="task.ui_description"
            class="inspector-detail-ui_description"
            v-html="renderMarkdown(task.ui_description)"
          ></div>
        </div>
        <div v-else key="closed">
          <p v-for="[label, value] in fields" :key="label" class="services-provider-field">
            <strong>{{ label }}:</strong> {{ value }}
          </p>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.inspector-detail-card { cursor: pointer; display: flex; flex-direction: column; border-radius: 8px; border: 1px solid #eee; background: #fafafa; overflow: hidden; margin: 0.75rem 0; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-task { background: #5c6bc0; }
.inspector-detail-badges { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inspector-detail-body { padding: 0.6rem 0.75rem; font-size: 0.8rem; color: #444; }
.inspector-detail-ui_description { margin: 0.5rem 0 0; line-height: 1.4; }
.crossfade-enter-active, .crossfade-leave-active { transition: opacity 0.15s ease; }
.crossfade-enter-from, .crossfade-leave-to { opacity: 0; }

/* Status flag colors — pending/dispatched/done/failed/canceled, same
   pill shape as .inspector-detail-badge above. */
.task-status-pending { background: #888; }
.task-status-dispatched { background: #4a6fa5; }
.task-status-done { background: #2e7d32; }
.task-status-failed { background: #c62828; }
.task-status-canceled { background: #999; }

/* Closed state: plain "label: value" text — same idiom as
   InspectorDetailCard.vue's own .inspector-detail-field, not the styled
   inputs below (those are for the open state only). */
.services-provider-field { margin: 0 0 0.4rem; line-height: 1.4; }
.services-provider-field:last-child { margin-bottom: 0; }
.services-provider-field strong { color: #555; margin-right: 0.3rem; }

/* Open state: the same field-as-disabled-input look every other Manage
   services tab uses (see ServicesView.vue's own identically-named rules
   — duplicated here since scoped styles don't cross component
   boundaries). */
.services-field { display: flex; flex-direction: column; gap: 0.25rem; margin: 0 0 0.75rem; max-width: 420px; }
.services-field:last-child { margin-bottom: 0; }
.services-field-label { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
.services-field-input { width: 100%; box-sizing: border-box; padding: 0.4rem 0.6rem; border: 1px solid #ddd; border-radius: 6px; background: #f5f5f7; color: #333; font: inherit; font-size: 0.85rem; }
.services-field-input:disabled { opacity: 1; cursor: default; -webkit-text-fill-color: #333; }
</style>
