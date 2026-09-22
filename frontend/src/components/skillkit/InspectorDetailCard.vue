<script setup>
import { computed, ref, watch } from 'vue'
import { vAutosize } from './textareaAutosize.js'
import CardMenu from './CardMenu.vue'
import ScriptEditDialog from './ScriptEditDialog.vue'
import { handleEnterNext } from './enterToNextField.js'
import { useFloatingTooltip } from '../../useFloatingTooltip.js'
import { customDialog } from '../../dialogStore.js'
import { useTokensBar } from '../../composables/useTokensBar.js'

const props = defineProps({
  selectedElement: { type: Object, default: null },
  editableFiles: { type: Array, default: null },
  firedActionEdge: { type: Object, default: null },
  highlightedStateKey: { type: String, default: null },
  selectable: { type: Boolean, default: false },
  closable: { type: Boolean, default: true },
  editable: { type: Boolean, default: false },
  stateTokens: { type: Number, default: null },
  availableStates: { type: Array, default: () => [] },
  open: { type: Boolean, default: false },
  recentlyAddedKey: { type: String, default: null },
  roleBadge: { type: String, default: null },
  saveField: { type: Function, default: null }
})

const emit = defineEmits(['select-attachment', 'jump-to-attachment', 'close', 'select', 'set-field', 'delete', 'update:open', 'open-actions-order', 'open-sources'])

const showEditForm = computed(() => props.editable && props.open)

const TOKENS_BAR_MAX = 1000
const { width: tokensBarWidth, level: tokensBarLevel } = useTokensBar(computed(() => props.stateTokens), TOKENS_BAR_MAX)
const {
  visible: tokensTooltipVisible, style: tokensTooltipStyle, show: showTokensTooltip, hide: hideTokensTooltip
} = useFloatingTooltip()

function handleCardClick() {
  if (props.editable) emit('update:open', !props.open)
  if (props.selectable) emit('select')
}

const editUiLabel = ref('')
const editUiDescription = ref('')
const editContextualPrompt = ref('')
const editTarget = ref('')

const elementIdentity = computed(() => {
  if (!props.selectedElement) return null
  const d = props.selectedElement.data
  return props.selectedElement.kind === 'state' ? `state:${d.id}` : `action:${d.matchStateKey}/${d.actionName}`
})

const isRecentlyAdded = computed(() => props.recentlyAddedKey != null && props.recentlyAddedKey === elementIdentity.value)

function resetEditBuffers() {
  if (!props.selectedElement) return
  const d = props.selectedElement.data
  editUiLabel.value = d.uiLabel ?? ''
  editUiDescription.value = d.uiDescription ?? ''
  editContextualPrompt.value = d.contextualPrompt ?? ''
  editTarget.value = d.target ?? ''
}

watch(elementIdentity, resetEditBuffers, { immediate: true })
watch(() => props.open, (isOpen) => { if (isOpen) resetEditBuffers() })

function commitTextField(field, currentValue, originalValue) {
  if (currentValue === originalValue) return
  emit('set-field', field, currentValue)
}

function commitUiLabel() {
  commitTextField('ui-label', editUiLabel.value, props.selectedElement?.data.uiLabel ?? '')
}

function commitUiDescription() {
  commitTextField('ui-description', editUiDescription.value, props.selectedElement?.data.uiDescription ?? '')
}

function commitContextualPrompt() {
  commitTextField('contextual-prompt', editContextualPrompt.value, props.selectedElement?.data.contextualPrompt ?? '')
}

function commitTarget() {
  commitTextField('target', editTarget.value, props.selectedElement?.data.target ?? '')
}

function openScriptDialog(tab) {
  const targetKey = props.selectedElement?.data.target
  const targetIsAiState = props.availableStates.find((s) => s.key === targetKey)?.inputProcessor === 'ai'
  customDialog({
    component: ScriptEditDialog,
    wide: true,
    props: {
      initialTrigger: props.selectedElement?.data.trigger ?? '',
      initialOnExit: props.selectedElement?.data.onExit ?? '',
      initialTask: props.selectedElement?.data.task ?? '',
      showTrigger: !props.selectedElement?.data.isInitEdge,
      targetIsAiState,
      initialTab: tab,
      onCommit: (field, value) => props.saveField(field, value)
    }
  })
}

function commitBoolField(field, value) {
  emit('set-field', field, value)
}

const isAiState = computed(() => props.selectedElement?.data.inputProcessor === 'ai')

const hasConfiguredSources = computed(() => {
  const d = props.selectedElement?.data
  return (d?.aiMayReadSources?.length ?? 0) + (d?.aiMustReadSources?.length ?? 0) > 0
})

const isDeleteDisabled = computed(() => {
  const d = props.selectedElement?.data
  if (!d) return false
  return props.selectedElement.kind === 'state' ? d.isStart : d.isInitEdge
})
const deleteDisabledReason = computed(() => {
  if (!isDeleteDisabled.value) return null
  return props.selectedElement.kind === 'state'
    ? "The initial state can't be deleted — point init-action at another state first."
    : "The init-action can't be deleted."
})

function handleDelete() {
  emit('delete')
}

const envEntries = computed(() => Object.entries(props.selectedElement?.data.env ?? {}))

function attachmentLabel(index) { return String.fromCharCode(97 + index) }

function stateLabelFor(key) {
  return props.availableStates.find((s) => s.key === key)?.uiLabel ?? key
}

const isSelectedActionFired = computed(() => {
  if (props.selectedElement?.kind !== 'action' || !props.firedActionEdge) return false
  return (
    props.selectedElement.data.matchStateKey === props.firedActionEdge.stateKey &&
    props.selectedElement.data.actionName === props.firedActionEdge.actionName
  )
})

const isSelectedStateCurrent = computed(() => {
  return props.selectedElement?.kind === 'state' && props.selectedElement.data.id === props.highlightedStateKey
})

const hasSelectedElementBadges = computed(() => {
  if (!props.selectedElement) return false
  if (props.selectedElement.kind === 'state') {
    const d = props.selectedElement.data
    if (showEditForm.value) return isAiState.value
    return !!props.roleBadge || isSelectedStateCurrent.value || d.isStart || d.final ||
      (isAiState.value && (!d.chatEnabled || d.historyCutoff || (d.reactionsEnabled && d.hasReactions)))
  }
  if (showEditForm.value) return true
  const d = props.selectedElement.data
  return isSelectedActionFired.value || !d.hasTrigger || d.isInitEdge || !!d.trigger || !!d.task || !!d.onExit
})

function selectAttachment(fileName) {
  if (props.selectedElement?.kind === 'state') emit('jump-to-attachment', fileName)
  else emit('select-attachment', fileName)
}
</script>

<template>
  <div
    v-if="selectedElement"
    class="inspector-detail-card"
    :class="{ 'inspector-detail-card-selectable': selectable, 'inspector-detail-card-editable': editable, 'inspector-detail-card-open': showEditForm, 'inspector-detail-card-flash': isRecentlyAdded }"
    @click="handleCardClick"
  >
    <div class="inspector-detail-header">
      <div class="inspector-detail-header-top">
        <span
          class="inspector-detail-badge"
          :class="selectedElement.kind === 'state' ? 'inspector-detail-badge-state' : 'inspector-detail-badge-action'"
        >{{ selectedElement.kind === 'state' ? 'State' : 'Action' }}</span>
        <input
          v-if="showEditForm"
          v-model="editUiLabel"
          class="inspector-detail-title-input"
          placeholder="Label"
          @click.stop
          @blur="commitUiLabel"
          @keydown.enter.prevent="handleEnterNext"
        />
        <span v-else class="inspector-detail-title">{{ selectedElement.data.uiLabel }}</span>
        <CardMenu v-if="editable" v-slot="{ close }">
          <button
            v-if="selectedElement.kind === 'state'"
            type="button"
            @click="close(); emit('open-actions-order')"
          >Actions order</button>
          <button
            type="button"
            class="card-menu-item-danger"
            :disabled="isDeleteDisabled"
            :title="deleteDisabledReason"
            @click="handleDelete"
          >Delete</button>
        </CardMenu>
        <button v-if="closable" class="close-x-btn" title="Close" @click.stop="emit('close')">×</button>
      </div>
      <div v-if="hasSelectedElementBadges" class="inspector-detail-badges">
        <template v-if="selectedElement.kind === 'state'">
          <template v-if="!showEditForm">
            <span v-if="roleBadge" class="inspector-detail-badge inspector-detail-badge-current">{{ roleBadge }}</span>
            <span v-if="isSelectedStateCurrent" class="inspector-detail-badge inspector-detail-badge-current">Current</span>
            <span v-if="selectedElement.data.isStart" class="inspector-detail-badge inspector-detail-badge-start">Init</span>
            <span v-if="selectedElement.data.final" class="inspector-detail-badge inspector-detail-badge-final">Final</span>
          </template>
          <template v-if="showEditForm">
            <span
              v-if="isAiState"
              class="inspector-detail-badge inspector-detail-badge-toggle"
              :class="!selectedElement.data.chatEnabled ? 'inspector-detail-badge-toggle-on' : 'inspector-detail-badge-toggle-off'"
              title="Click to toggle"
              @click.stop="commitBoolField('chat-enabled', !selectedElement.data.chatEnabled)"
            >No chat</span>
            <span
              v-if="isAiState"
              class="inspector-detail-badge inspector-detail-badge-toggle"
              :class="selectedElement.data.historyCutoff ? 'inspector-detail-badge-toggle-on' : 'inspector-detail-badge-toggle-off'"
              title="Click to toggle"
              @click.stop="commitBoolField('history-cutoff', !selectedElement.data.historyCutoff)"
            >History cutoff</span>
            <span
              v-if="isAiState"
              class="inspector-detail-badge inspector-detail-badge-toggle"
              :class="[
                selectedElement.data.reactionsEnabled ? 'inspector-detail-badge-toggle-on' : 'inspector-detail-badge-toggle-off',
                { 'inspector-detail-badge-toggle-locked': !selectedElement.data.hasReactions }
              ]"
              :title="selectedElement.data.hasReactions ? 'Click to toggle' : 'This project declares no reactions — add one in the Reactions tab first.'"
              @click.stop="selectedElement.data.hasReactions && commitBoolField('reactions-enabled', !selectedElement.data.reactionsEnabled)"
            >Reactions</span>
          </template>
          <template v-else>
            <span v-if="isAiState && !selectedElement.data.chatEnabled" class="inspector-detail-badge inspector-detail-badge-neutral">No chat</span>
            <span v-if="isAiState && selectedElement.data.historyCutoff" class="inspector-detail-badge inspector-detail-badge-neutral">History cutoff</span>
            <span v-if="isAiState && selectedElement.data.reactionsEnabled && selectedElement.data.hasReactions" class="inspector-detail-badge inspector-detail-badge-neutral">Reactions</span>
          </template>
          <button
            v-if="isAiState && (showEditForm || hasConfiguredSources)"
            type="button"
            class="inspector-detail-badge inspector-detail-badge-toggle inspector-sources-badge-btn"
            :class="hasConfiguredSources ? 'inspector-detail-badge-toggle-on' : 'inspector-detail-badge-toggle-off'"
            :disabled="!editable"
            title="What this state may access"
            @click.stop="emit('open-sources')"
          >Sources</button>
        </template>
        <template v-else>
          <button
            v-if="!selectedElement.data.isInitEdge && (showEditForm || selectedElement.data.trigger)"
            type="button"
            class="inspector-detail-badge inspector-detail-badge-toggle inspector-detail-badge-trigger-btn"
            :class="selectedElement.data.trigger ? ['inspector-detail-badge-toggle-on', 'inspector-detail-badge-trigger'] : 'inspector-detail-badge-toggle-off'"
            :disabled="!editable"
            title="Trigger"
            @click.stop="openScriptDialog('trigger')"
          >Trigger</button>
          <button
            v-if="showEditForm || selectedElement.data.onExit"
            type="button"
            class="inspector-detail-badge inspector-detail-badge-toggle inspector-detail-badge-onexit-btn"
            :class="selectedElement.data.onExit ? ['inspector-detail-badge-toggle-on', 'inspector-detail-badge-onexit'] : 'inspector-detail-badge-toggle-off'"
            :disabled="!editable"
            title="On exit"
            @click.stop="openScriptDialog('on-exit')"
          >On exit</button>
          <button
            v-if="showEditForm || selectedElement.data.task"
            type="button"
            class="inspector-detail-badge inspector-detail-badge-toggle inspector-detail-badge-task-btn"
            :class="selectedElement.data.task ? ['inspector-detail-badge-toggle-on', 'inspector-detail-badge-task'] : 'inspector-detail-badge-toggle-off'"
            :disabled="!editable"
            title="Task"
            @click.stop="openScriptDialog('task')"
          >Task</button>
          <template v-if="!showEditForm">
            <span v-if="selectedElement.data.isInitEdge" class="inspector-detail-badge inspector-detail-badge-start">Start</span>
            <span v-if="isSelectedActionFired" class="inspector-detail-badge inspector-detail-badge-fired">Fired</span>
            <span v-if="!selectedElement.data.hasTrigger" class="inspector-detail-badge inspector-detail-badge-manual">Manual</span>
          </template>
        </template>
      </div>
    </div>
    <div class="inspector-detail-body">
      <template v-if="selectedElement.kind === 'state'">
        <Transition name="crossfade" mode="out-in">
          <div v-if="showEditForm" key="edit" class="inspector-detail-form">
            <label class="inspector-detail-form-label">Description</label>
            <textarea
              v-model="editUiDescription"
              v-autosize
              class="inspector-detail-textarea"
              rows="2"
              @click.stop
              @blur="commitUiDescription"
            ></textarea>
            <label v-if="isAiState" class="inspector-detail-form-label">
              <span class="inspector-ai-field-icon" title="Read by the AI">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              Contextual prompt
            </label>
            <textarea
              v-if="isAiState"
              v-model="editContextualPrompt"
              v-autosize
              class="inspector-detail-textarea"
              rows="2"
              @click.stop
              @blur="commitContextualPrompt"
            ></textarea>
            <div v-if="isAiState && stateTokens != null" class="inspector-detail-tokens">
              <span class="inspector-ai-field-icon" title="Estimated by the AI provider">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              <span class="inspector-detail-tokens-label">Tokens</span>
              <div
                class="inspector-detail-tokens-bar-track"
                @mouseenter="showTokensTooltip($event.currentTarget)"
                @mouseleave="hideTokensTooltip"
              >
                <div
                  class="inspector-detail-tokens-bar-fill"
                  :class="`inspector-detail-tokens-bar-fill-${tokensBarLevel}`"
                  :style="{ width: tokensBarWidth }"
                ></div>
              </div>
            </div>
          </div>
          <div v-else key="readonly" class="inspector-detail-readonly">
            <p v-if="selectedElement.data.uiDescription" class="inspector-detail-ui_description">{{ selectedElement.data.uiDescription }}</p>
            <div v-if="isAiState && stateTokens != null" class="inspector-detail-tokens">
              <span class="inspector-ai-field-icon" title="Estimated by the AI provider">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              <span class="inspector-detail-tokens-label">Tokens</span>
              <div
                class="inspector-detail-tokens-bar-track"
                @mouseenter="showTokensTooltip($event.currentTarget)"
                @mouseleave="hideTokensTooltip"
              >
                <div
                  class="inspector-detail-tokens-bar-fill"
                  :class="`inspector-detail-tokens-bar-fill-${tokensBarLevel}`"
                  :style="{ width: tokensBarWidth }"
                ></div>
              </div>
            </div>
          </div>
        </Transition>
      </template>
      <template v-else>
        <Transition name="crossfade" mode="out-in">
          <div v-if="showEditForm" key="edit" class="inspector-detail-form">
            <label class="inspector-detail-form-label">Description</label>
            <textarea
              v-model="editUiDescription"
              v-autosize
              class="inspector-detail-textarea"
              rows="2"
              @click.stop
              @blur="commitUiDescription"
            ></textarea>
            <p class="inspector-detail-field">
              <template v-if="!selectedElement.data.isInitEdge"><strong>{{ stateLabelFor(selectedElement.data.source) }}</strong> → </template>
              <select
                v-model="editTarget"
                class="inspector-detail-target-select"
                @click.stop
                @change="commitTarget"
              >
                <option v-for="state in availableStates" :key="state.key" :value="state.key">{{ state.uiLabel }}</option>
              </select>
            </p>
          </div>
          <div v-else key="readonly" class="inspector-detail-readonly">
            <p v-if="selectedElement.data.uiDescription" class="inspector-detail-ui_description">{{ selectedElement.data.uiDescription }}</p>
            <p class="inspector-detail-field"><template v-if="!selectedElement.data.isInitEdge"><strong>{{ stateLabelFor(selectedElement.data.source) }}</strong> → </template><strong>{{ stateLabelFor(selectedElement.data.target) }}</strong></p>
            <p v-if="envEntries.length" class="inspector-detail-field">
              <strong>Env:</strong>
              <code v-for="[key, value] in envEntries" :key="key" class="inspector-detail-code">{{ key }} = {{ value }}</code>
            </p>
          </div>
        </Transition>
      </template>
      <div v-if="showEditForm && selectedElement.data.attachments?.length" class="inspector-attachments">
        <button
          v-for="(fileName, idx) in selectedElement.data.attachments"
          :key="fileName"
          class="inspector-attachment-btn"
          :class="{ 'inspector-attachment-btn-disabled': editableFiles && !editableFiles.includes(fileName) }"
          :disabled="editableFiles && !editableFiles.includes(fileName)"
          :title="!editableFiles || editableFiles.includes(fileName) ? fileName : `${fileName} (not text-editable)`"
          @click.stop="selectAttachment(fileName)"
        >{{ attachmentLabel(idx) }}</button>
      </div>
    </div>
    <Teleport to="body">
      <span v-if="tokensTooltipVisible" class="inspector-detail-tokens-tooltip-floating" :style="tokensTooltipStyle">{{ stateTokens }} tokens</span>
    </Teleport>
  </div>
</template>

<style scoped>
.inspector-detail-card { flex-shrink: 0; margin-top: 0.75rem; max-height: 45%; display: flex; flex-direction: column; border-radius: 8px; border: 1px solid #eee; background: #fafafa; overflow: hidden; }
@keyframes inspector-detail-card-flash { from { background-color: #fff3b0; } to { background-color: #fafafa; } }
.inspector-detail-card-flash { animation: inspector-detail-card-flash 1.5s ease-out; }
.inspector-detail-card-selectable { cursor: pointer; }
.inspector-detail-card-selectable:hover { border-color: #c9d6e8; background: #f0f4fa; }
.inspector-detail-card-editable { max-height: none; overflow: visible; }
.inspector-detail-card-editable .inspector-detail-body { overflow: visible; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-card-editable .inspector-detail-header { cursor: pointer; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badges { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-state { background: #4a6fa5; }
.inspector-detail-badge-action { background: #8a6d3b; }
.inspector-detail-badge-current { background: #f5a623; color: #3a2600; }
.inspector-detail-badge-start { background: #2e7d32; }
.inspector-detail-badge-fired { background: #ad1457; }
.inspector-detail-badge-final { background: #c62828; }
.inspector-detail-badge-manual { background: #00695c; }
.inspector-detail-badge-neutral { background: #4a6fa5; }
.inspector-detail-badge-toggle { cursor: pointer; }
.inspector-sources-badge-btn { border: none; font-family: inherit; }
.inspector-detail-badge-toggle-off { background: #ccc; color: #555; }
.inspector-detail-badge-toggle-on { background: #4a6fa5; }
.inspector-detail-badge-toggle-locked { cursor: not-allowed; opacity: 0.5; }
.inspector-detail-badge-trigger-btn { appearance: none; border: none; margin: 0; font-family: inherit; cursor: pointer; }
.inspector-detail-badge-trigger-btn:disabled { cursor: not-allowed; opacity: 0.6; }
.inspector-detail-badge-trigger.inspector-detail-badge-toggle-on { background: #4b8bbe; }
.inspector-detail-badge-onexit-btn { appearance: none; border: none; margin: 0; font-family: inherit; cursor: pointer; }
.inspector-detail-badge-onexit-btn:disabled { cursor: not-allowed; opacity: 0.6; }
.inspector-detail-badge-onexit.inspector-detail-badge-toggle-on { background: #00838f; }
.inspector-detail-badge-task-btn { appearance: none; border: none; margin: 0; font-family: inherit; cursor: pointer; }
.inspector-detail-badge-task-btn:disabled { cursor: not-allowed; opacity: 0.6; }
.inspector-detail-badge-task.inspector-detail-badge-toggle-on { background: #7c4dff; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inspector-detail-title-input { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; border: 1px solid transparent; border-radius: 4px; padding: 0.1rem 0.3rem; background: transparent; }
.inspector-detail-title-input:hover, .inspector-detail-title-input:focus { border-color: #ccc; background: white; }
.inspector-detail-form-label { display: flex; align-items: center; gap: 0.35rem; margin: 20px 0 0.2rem; font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em; color: #777; }
.inspector-ai-field-icon { display: inline-flex; flex-shrink: 0; color: #8b5cf6; }
.inspector-detail-textarea { display: block; width: 100%; box-sizing: border-box; resize: vertical; font: inherit; font-size: 0.8rem; line-height: 1.54; padding: 0.4rem 0.5rem; border-radius: 6px; border: 1px solid #ccc; }
.inspector-detail-target-select { display: inline-block; width: auto; max-width: 100%; font: inherit; font-weight: 700; font-size: inherit; color: #333; padding: 0.05rem 0.2rem; border-radius: 4px; border: 1px solid transparent; background: transparent; cursor: pointer; }
.inspector-detail-target-select:hover, .inspector-detail-target-select:focus { border-color: #ccc; background: white; }
.close-x-btn { flex-shrink: 0; width: 1.4rem; height: 1.4rem; line-height: 1; border: none; border-radius: 6px; background: none; color: #666; cursor: pointer; font-size: 1rem; }
.close-x-btn:hover { background: #eee; }
.inspector-detail-body { padding: 0.6rem 0.75rem; overflow-y: auto; font-size: 0.8rem; color: #444; }
.inspector-detail-ui_description { margin: 0 0 0.5rem; line-height: 1.4; }
.inspector-detail-tokens { display: flex; align-items: center; gap: 0.4rem; margin: 0.4rem 0 0; }
.inspector-detail-tokens-label { flex-shrink: 0; font-size: 0.72rem; color: #888; }
.inspector-detail-tokens-bar-track { position: relative; flex: 1; min-width: 40px; height: 8px; border-radius: 999px; background: #eee; overflow: hidden; cursor: default; }
.inspector-detail-tokens-bar-fill { height: 100%; border-radius: 999px; transition: width 0.3s ease; }
.inspector-detail-tokens-bar-fill-green { background: #2e7d32; }
.inspector-detail-tokens-bar-fill-orange { background: #f5a623; }
.inspector-detail-tokens-bar-fill-red { background: #c62828; }
.inspector-detail-field { margin: 0 0 0.4rem; line-height: 1.4; }
.inspector-detail-code { display: inline-block; margin: 0.15rem 0.3rem 0 0; font-size: 0.75rem; background: #eee; border-radius: 4px; padding: 0.1rem 0.4rem; }
.inspector-attachments { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.5rem; }
.inspector-attachment-btn { width: 1.5rem; height: 1.5rem; line-height: 1; border-radius: 4px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; font-size: 0.72rem; font-weight: 600; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.inspector-attachment-btn:hover:not(:disabled) { background: #4a6fa5; color: white; }
.inspector-attachment-btn-disabled { border-color: #ccc; color: #aaa; cursor: not-allowed; }
.inspector-attachment-btn-disabled:hover { background: white; color: #aaa; }
.crossfade-enter-active, .crossfade-leave-active { transition: opacity 0.15s ease; }
.crossfade-enter-from, .crossfade-leave-to { opacity: 0; }
</style>

<style>
.inspector-detail-tokens-tooltip-floating {
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
