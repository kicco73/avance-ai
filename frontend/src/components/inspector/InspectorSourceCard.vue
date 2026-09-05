<script setup>
// The Inspector "Info" tab's card for a selected design-tree Source node
// (see FileExplorer.vue's own "Sources" branch) — same badge/title/edit-form
// convention as InspectorProjectCard.vue, but always showing its edit form
// (a source has nothing worth a read-only view): a renameable id, ui-label
// (the title), ui-description, and a driver dropdown (today, only "Avance
// Archive"). Unlike an ordinary attachment, there's no file to pick here —
// every source gets its own sources/<id>.csv archive automatically (see
// ProjectEditor.add_source), edited via the design panel's own
// SourceContentPanel.vue when this source is selected, not from this card.
import { ref, watch } from 'vue'
import { vAutosize } from './textareaAutosize.js'
import { handleEnterNext } from './enterToNextField.js'
import CardMenu from './CardMenu.vue'

const props = defineProps({
  // { name, ui_label, ui_description, ai_definition, url } | null, from getProjectSources
  source: { type: Object, default: null },
  deleting: { type: Boolean, default: false }
})

const emit = defineEmits(['set-field', 'delete'])

// Only one driver exists today — "avance", url's own scheme — so this is
// a single-option dropdown by design, not a stand-in for a missing feature.
const DRIVER_OPTIONS = [{ value: 'avance', label: 'Avance Embedded' }]

const editUiLabel = ref('')
const editUiDescription = ref('')
const editAiDefinition = ref('')
const editId = ref('')

function resetEditBuffers() {
  editUiLabel.value = props.source?.ui_label ?? ''
  editUiDescription.value = props.source?.ui_description ?? ''
  editAiDefinition.value = props.source?.ai_definition ?? ''
  editId.value = props.source?.name ?? ''
}

watch(() => props.source, resetEditBuffers, { immediate: true, deep: true })

function commitTextField(field, currentValue, originalValue) {
  if (currentValue === originalValue) return
  emit('set-field', field, currentValue)
}

function commitUiLabel() {
  commitTextField('ui-label', editUiLabel.value, props.source?.ui_label ?? '')
}

function commitUiDescription() {
  commitTextField('ui-description', editUiDescription.value, props.source?.ui_description ?? '')
}

function commitAiDefinition() {
  commitTextField('ai-definition', editAiDefinition.value, props.source?.ai_definition ?? '')
}

function commitId() {
  commitTextField('name', editId.value, props.source?.name ?? '')
}

function handleDelete() {
  emit('delete')
}
</script>

<template>
  <div class="inspector-detail-card inspector-source-card">
    <div class="inspector-detail-header">
      <div class="inspector-detail-header-top">
        <span class="inspector-detail-badge inspector-detail-badge-source">Source</span>
        <input
          v-model="editUiLabel"
          class="inspector-detail-title-input"
          placeholder="Label"
          @click.stop
          @blur="commitUiLabel"
          @keydown.enter.prevent="handleEnterNext"
        />
        <CardMenu>
          <button
            type="button"
            class="card-menu-item-danger"
            :disabled="deleting"
            @click="handleDelete"
          >{{ deleting ? 'Deleting…' : 'Delete' }}</button>
        </CardMenu>
      </div>
    </div>
    <div class="inspector-detail-body">
      <div class="inspector-detail-form">
        <label class="inspector-detail-form-label">Description</label>
        <textarea
          v-model="editUiDescription"
          v-autosize
          class="inspector-detail-textarea"
          rows="2"
          @click.stop
          @blur="commitUiDescription"
        ></textarea>

        <label class="inspector-detail-form-label">
          <span class="inspector-ai-field-icon" title="Read by the AI">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
          </span>
          AI definition
        </label>
        <textarea
          v-model="editAiDefinition"
          v-autosize
          class="inspector-detail-textarea"
          rows="3"
          placeholder="What this file contains and how the model should search it — e.g. which values to combine to get a single row. Required once this source is listed in a state's own ai-may-query-sources/ai-must-query-sources."
          @click.stop
          @blur="commitAiDefinition"
        ></textarea>

        <label class="inspector-detail-form-label" title="Referenced as source.<id> in a trigger/env expression">
          <span class="inspector-py-field-icon" title="Identifier">ID</span>
          Id
        </label>
        <input
          v-model="editId"
          class="inspector-project-id-input"
          placeholder="e.g. flight_records"
          @click.stop
          @blur="commitId"
          @keydown.enter.prevent="handleEnterNext"
        />

        <label class="inspector-detail-form-label">Driver</label>
        <select class="inspector-source-select" :value="'avance'">
          <option v-for="option in DRIVER_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* No max-height cap here (unlike other cards reusing this same class
   name in their own scoped styles): this card is the Info tab's only
   content while a source is selected (see InspectorStateTab.vue's own
   isSourceContext, which hides every other card), so it should use
   whatever height the tab actually has, not an arbitrary fraction of it. */
.inspector-source-card { margin-top: 0; cursor: default; }
.inspector-detail-card { display: flex; flex-direction: column; border-radius: 8px; border: 1px solid #eee; background: #fafafa; overflow: visible; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-source { background: #3949ab; }
.inspector-detail-title-input { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; border: 1px solid transparent; border-radius: 4px; padding: 0.1rem 0.3rem; background: transparent; }
.inspector-detail-title-input:hover, .inspector-detail-title-input:focus { border-color: #ccc; background: white; }
.inspector-detail-form-label { display: flex; align-items: center; gap: 0.35rem; margin: 20px 0 0.2rem; font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
.inspector-py-field-icon { display: inline-flex; flex-shrink: 0; align-items: center; justify-content: center; width: 1.1rem; height: 0.85rem; border-radius: 3px; background: #4b8bbe; color: white; font-size: 0.55rem; font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: -0.02em; }
/* Marks a field the AI itself reads, as opposed to a purely human-facing
   one like Description — same convention as InspectorDetailCard.vue's own. */
.inspector-ai-field-icon { display: inline-flex; flex-shrink: 0; color: #8b5cf6; }
.inspector-project-id-input { display: block; width: 100%; box-sizing: border-box; font: inherit; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.8rem; padding: 0.3rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-source-select { display: block; width: 100%; box-sizing: border-box; font: inherit; font-size: 0.8rem; padding: 0.3rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; background: white; }
.inspector-detail-textarea { display: block; width: 100%; box-sizing: border-box; resize: vertical; font: inherit; font-size: 0.8rem; line-height: 1.54; padding: 0.4rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-detail-body { padding: 0.6rem 0.75rem; overflow-y: auto; font-size: 0.8rem; color: #444; }
</style>
