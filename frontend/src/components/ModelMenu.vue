<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import {
  aiModels,
  aiModelAuto,
  aiModelCurrentIndex,
  aiModelSelectionLoading,
  selectAiModel
} from '../chatStore.js'

const VIEWPORT_MARGIN = 8

const open = ref(false)
const rootEl = ref(null)
const btnEl = ref(null)
const panelEl = ref(null)
const panelStyle = ref({})

// Reads chatStore.js's shared aiModels/aiModelAuto/aiModelCurrentIndex —
// loaded once at boot (App.vue's loadAiModels) and kept in sync by every
// chat turn/action response (see chatStore.js's submitMessage/handleAction),
// so this component never fetches on its own.
const currentLabel = computed(() => aiModels.value[aiModelCurrentIndex.value]?.ui_label ?? 'Model')
const buttonLabel = computed(() => (aiModelAuto.value ? `Auto: ${currentLabel.value}` : currentLabel.value))

// The panel is teleported to <body> (see template) so it can't be clipped
// by an ancestor's `overflow: hidden` (e.g. EditProjectView's chat panel) —
// then positioned/clamped here against the actual viewport instead of
// relying on CSS alone, and given its own scrollbar for whatever still
// doesn't fit. Opens downward from the button, or upward if there isn't
// enough room below.
async function positionPanel() {
  await nextTick()
  const btn = btnEl.value
  const panel = panelEl.value
  if (!btn || !panel) return

  const btnRect = btn.getBoundingClientRect()
  const spaceBelow = window.innerHeight - btnRect.bottom - VIEWPORT_MARGIN
  const spaceAbove = btnRect.top - VIEWPORT_MARGIN
  const openUpward = spaceBelow < panel.offsetHeight && spaceAbove > spaceBelow

  panelStyle.value = {
    left: `${Math.max(VIEWPORT_MARGIN, btnRect.left)}px`,
    maxHeight: `${Math.max(120, openUpward ? spaceAbove : spaceBelow)}px`,
    ...(openUpward
      ? { bottom: `${window.innerHeight - btnRect.top + 4}px` }
      : { top: `${btnRect.bottom + 4}px` })
  }
}

async function toggle() {
  open.value = !open.value
  if (open.value) await positionPanel()
}

function close() {
  open.value = false
}

// Both the "Auto" entry and each model row call this with either `null`
// (auto) or an aiModels[] index — chatStore.js's selectAiModel is the only
// place that relays the choice to the backend (AiService.select_model is
// what actually translates it into a provider) and refreshes the shared
// state from its response.
async function select(index) {
  if (aiModelSelectionLoading.value) return
  if (index === (aiModelAuto.value ? null : aiModelCurrentIndex.value)) {
    close()
    return
  }
  await selectAiModel(index)
  close()
}

function handleClickOutside(event) {
  if (!open.value) return
  if (rootEl.value?.contains(event.target)) return
  if (panelEl.value?.contains(event.target)) return
  close()
}
document.addEventListener('click', handleClickOutside, true)
window.addEventListener('resize', close)
window.addEventListener('scroll', close, true)
onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside, true)
  window.removeEventListener('resize', close)
  window.removeEventListener('scroll', close, true)
})
</script>

<template>
  <div class="model-menu" ref="rootEl">
    <button ref="btnEl" class="model-btn" :title="buttonLabel" @click="toggle">
      {{ buttonLabel }}
    </button>

    <Teleport to="body">
      <div v-if="open" ref="panelEl" class="model-panel" :style="panelStyle">
        <ul class="model-list">
          <li>
            <button
              class="model-item"
              :disabled="aiModelSelectionLoading"
              @click="select(null)"
            >
              <span class="model-item-check">{{ aiModelAuto ? '✓' : '' }}</span>
              <span class="model-item-label">Auto ({{ currentLabel }})</span>
            </button>
          </li>
          <li v-for="(m, i) in aiModels" :key="`${m.name}/${m.model}`">
            <button
              class="model-item"
              :disabled="aiModelSelectionLoading"
              @click="select(i)"
            >
              <span class="model-item-check">{{ !aiModelAuto && i === aiModelCurrentIndex ? '✓' : '' }}</span>
              <div class="model-item-text">
                <span class="model-item-label">{{ m.ui_label }}</span>
              </div>
            </button>
          </li>
        </ul>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.model-menu {
  position: relative;
}

.model-btn {
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  max-width: 160px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.model-btn:hover {
  background: #4a6fa5;
  color: white;
}
</style>

<style>
/* Unscoped: the panel lives under <body> via Teleport, outside this
   component's normal DOM subtree, so a scoped [data-v-xxx] attribute
   selector would never match it. */
.model-panel {
  position: fixed;
  min-width: 220px;
  max-width: 320px;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 1000;
  overflow-y: auto;
}

.model-list {
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
}

.model-item {
  display: flex;
  align-items: flex-start;
  gap: 0.3rem;
  width: 100%;
  padding: 0.5rem 0.9rem;
  border: none;
  background: none;
  font-size: 0.9rem;
  color: #333;
  text-align: left;
  cursor: pointer;
}

.model-item:hover:not(:disabled) {
  background: #f0f4fa;
}

.model-item:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

li + li .model-item {
  border-top: 1px solid #eee;
}

.model-item-check {
  flex: none;
  display: inline-block;
  width: 1.1rem;
  color: #2e7d32;
  font-weight: 600;
}

.model-item-text {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  min-width: 0;
}

.model-item-label {
  overflow-wrap: anywhere;
}

</style>
