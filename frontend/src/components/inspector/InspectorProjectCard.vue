<script setup>
// A reusable detail card for the project's top-level `project:` section, using
// the same badge/title/edit-form convention as InspectorDetailCard.vue's state/action
// cards but as its own component: a project has no attachments, delete, or Graph-selection identity.
import { computed, ref, watch } from 'vue'
import { vAutosize } from './textareaAutosize.js'
import { handleEnterNext } from './enterToNextField.js'

const props = defineProps({
  project: { type: Object, default: null }, // { id, ui_label, ui_description, talk_enabled, signal_tracking_on_ai_message } | null, from getProjectMetadata
  editable: { type: Boolean, default: false }
})

const emit = defineEmits(['set-field'])

const open = ref(false)
const showEditForm = computed(() => props.editable && open.value)

const editUiLabel = ref('')
const editUiDescription = ref('')
const editId = ref('')

function resetEditBuffers() {
  editUiLabel.value = props.project?.ui_label ?? ''
  editUiDescription.value = props.project?.ui_description ?? ''
  editId.value = props.project?.id ?? ''
}

watch(() => props.project, resetEditBuffers, { immediate: true, deep: true })
// Same "reopen always starts from whatever's actually current" reasoning
// as InspectorDetailCard.vue's own identically-named watch.
watch(open, (isOpen) => { if (isOpen) resetEditBuffers() })

function handleCardClick() {
  if (props.editable) open.value = !open.value
}

function commitTextField(field, currentValue, originalValue) {
  if (currentValue === originalValue) return
  emit('set-field', field, currentValue)
}

function commitUiLabel() {
  commitTextField('ui-label', editUiLabel.value, props.project?.ui_label ?? '')
}

function commitUiDescription() {
  commitTextField('ui-description', editUiDescription.value, props.project?.ui_description ?? '')
}

// A falsy id round-trips as "no id declared" rather than the literal empty
// string (see AutomatonYamlEditor.set_project_field) — "" is never a valid
// identifier, so writing it through as-is would bounce back as a 400.
function commitId() {
  commitTextField('id', editId.value, props.project?.id ?? '')
}

function commitBoolField(field, value) {
  emit('set-field', field, value)
}
</script>

<template>
  <div
    class="inspector-detail-card inspector-project-card"
    :class="{ 'inspector-detail-card-editable': editable, 'inspector-detail-card-open': showEditForm }"
    @click="handleCardClick"
  >
    <div class="inspector-detail-header">
      <div class="inspector-detail-header-top">
        <span class="inspector-detail-badge inspector-detail-badge-project">Project</span>
        <input
          v-if="showEditForm"
          v-model="editUiLabel"
          class="inspector-detail-title-input"
          placeholder="Label"
          @click.stop
          @blur="commitUiLabel"
          @keydown.enter.prevent="handleEnterNext"
        />
        <span v-else class="inspector-detail-title">{{ project?.ui_label || 'Untitled project' }}</span>
      </div>
      <div v-if="showEditForm" class="inspector-detail-badges">
        <span
          class="inspector-detail-badge inspector-detail-badge-toggle"
          :class="(project?.talk_enabled ?? true) ? 'inspector-detail-badge-toggle-on' : 'inspector-detail-badge-toggle-off'"
          title="Click to toggle"
          @click.stop="commitBoolField('talk-enabled', !(project?.talk_enabled ?? true))"
        >Talk enabled</span>
        <span
          class="inspector-detail-badge inspector-detail-badge-toggle"
          :class="project?.signal_tracking_on_ai_message ? 'inspector-detail-badge-toggle-on' : 'inspector-detail-badge-toggle-off'"
          title="Click to toggle"
          @click.stop="commitBoolField('signal-tracking-on-ai-message', !project?.signal_tracking_on_ai_message)"
        >Track on AI</span>
      </div>
    </div>
    <div class="inspector-detail-body">
      <Transition name="crossfade" mode="out-in">
        <div v-if="showEditForm" key="edit" class="inspector-detail-form">
          <label class="inspector-detail-form-label" title="Referenced by other projects as automaton.<id>">
            <span class="inspector-py-field-icon" title="Identifier">ID</span>
            Id
          </label>
          <input
            v-model="editId"
            class="inspector-project-id-input"
            placeholder="e.g. concierge"
            @click.stop
            @blur="commitId"
            @keydown.enter.prevent="handleEnterNext"
          />
          <label class="inspector-detail-form-label">Description</label>
          <textarea
            v-model="editUiDescription"
            v-autosize
            class="inspector-detail-textarea"
            rows="2"
            @click.stop
            @blur="commitUiDescription"
          ></textarea>
        </div>
        <div v-else key="readonly" class="inspector-detail-readonly">
          <p v-if="project?.id" class="inspector-detail-field"><strong>Id:</strong> <code class="inspector-detail-code">{{ project.id }}</code></p>
          <p v-if="project?.ui_description" class="inspector-detail-ui_description">{{ project.ui_description }}</p>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.inspector-project-card { margin-top: 0; cursor: default; }
.inspector-project-card.inspector-detail-card-editable { cursor: pointer; }
.inspector-detail-card { max-height: 45%; display: flex; flex-direction: column; border-radius: 8px; border: 1px solid #eee; background: #fafafa; overflow: hidden; }
.inspector-detail-card-editable.inspector-detail-card-open { max-height: none; overflow: visible; }
.inspector-detail-card-editable.inspector-detail-card-open .inspector-detail-body { overflow: visible; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-card-editable .inspector-detail-header { cursor: pointer; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-project { background: #6a1b9a; }
.inspector-detail-badges { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.inspector-detail-badge-toggle { cursor: pointer; }
.inspector-detail-badge-toggle-off { background: #ccc; color: #555; }
.inspector-detail-badge-toggle-on { background: #4a6fa5; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inspector-detail-title-input { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; border: 1px solid transparent; border-radius: 4px; padding: 0.1rem 0.3rem; background: transparent; }
.inspector-detail-title-input:hover, .inspector-detail-title-input:focus { border-color: #ccc; background: white; }
.inspector-detail-form-label { display: flex; align-items: center; gap: 0.35rem; margin: 20px 0 0.2rem; font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
.inspector-py-field-icon { display: inline-flex; flex-shrink: 0; align-items: center; justify-content: center; width: 1.1rem; height: 0.85rem; border-radius: 3px; background: #4b8bbe; color: white; font-size: 0.55rem; font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; letter-spacing: -0.02em; }
.inspector-project-id-input { display: block; width: 100%; box-sizing: border-box; font: inherit; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.8rem; padding: 0.3rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-detail-textarea { display: block; width: 100%; box-sizing: border-box; resize: vertical; font: inherit; font-size: 0.8rem; line-height: 1.54; padding: 0.4rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-detail-body { padding: 0.6rem 0.75rem; overflow-y: auto; font-size: 0.8rem; color: #444; }
.inspector-detail-ui_description { margin: 0 0 0.5rem; line-height: 1.4; }
.inspector-detail-field { margin: 0 0 0.4rem; line-height: 1.4; }
.inspector-detail-code { font-size: 0.75rem; background: #eee; border-radius: 4px; padding: 0.1rem 0.4rem; }
.crossfade-enter-active, .crossfade-leave-active { transition: opacity 0.15s ease; }
.crossfade-enter-from, .crossfade-leave-to { opacity: 0; }
</style>
