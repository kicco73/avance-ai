<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
import { renderMarkdown } from '../markdown.js'
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

// Reads chatStore.js's shared model state, kept in sync by every chat
// turn/action response — this component never fetches on its own.
const currentModel = computed(() => aiModels.value[aiModelCurrentIndex.value] ?? null)
const currentLabel = computed(() => currentModel.value?.ui_label ?? 'Model')
const buttonLabel = computed(() => (aiModelAuto.value ? `Auto: ${currentLabel.value}` : currentLabel.value))

const infoOpen = ref(false)

// The panel is teleported to <body> so it can't be clipped by an
// ancestor's `overflow: hidden`, then positioned/clamped here against the
// viewport. Opens downward from the button, or upward if there isn't
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

// Called with either `null` (auto) or an aiModels[] index —
// selectAiModel relays the choice to the backend and refreshes the
// shared state from its response.
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
      <span class="model-btn-label">{{ buttonLabel }}</span>
      <span class="model-btn-caret">▾</span>
    </button>
    <button
      class="model-info-btn"
      title="About the current model"
      :disabled="!currentModel"
      @click="infoOpen = true"
    >?</button>

    <Teleport to="body">
      <div v-if="infoOpen" class="model-info-overlay" @click.self="infoOpen = false">
        <div class="model-info-dialog">
          <div class="model-info-header">
            <span class="model-info-title">{{ currentModel?.ui_label }}</span>
            <button class="model-info-close-btn" title="Close" @click="infoOpen = false">×</button>
          </div>
          <p class="model-info-field"><strong>Driver:</strong> {{ currentModel?.driver }}</p>
          <p class="model-info-field"><strong>Model:</strong> {{ currentModel?.model }}</p>
          <p v-if="currentModel?.url" class="model-info-field"><strong>Url:</strong> {{ currentModel.url }}</p>
          <div
            v-if="currentModel?.ui_description"
            class="model-info-description"
            v-html="renderMarkdown(currentModel.ui_description)"
          ></div>
        </div>
      </div>
    </Teleport>

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
          <li v-for="(m, i) in aiModels" :key="`${m.driver}/${m.model}`">
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
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.model-btn {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  max-width: 160px;
}

.model-btn-label {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.model-btn-caret {
  flex: none;
  font-size: 0.65rem;
}

.model-btn:hover {
  background: #4a6fa5;
  color: white;
}

.model-info-btn {
  flex: none;
  width: 1.6rem;
  height: 1.6rem;
  line-height: 1;
  padding: 0;
  border-radius: 50%;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 600;
}

.model-info-btn:hover:not(:disabled) {
  background: #4a6fa5;
  color: white;
}

.model-info-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>

<style>
/* Unscoped: the panel lives under <body> via Teleport, outside this
   component's normal DOM subtree, so a scoped [data-v-xxx] attribute
   selector would never match it. */
.model-info-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 1001;
  display: flex;
  align-items: center;
  justify-content: center;
}

.model-info-dialog {
  background: white;
  border-radius: 10px;
  padding: 1rem 1.2rem;
  max-width: 420px;
  width: calc(100% - 2rem);
  max-height: calc(100vh - 4rem);
  overflow-y: auto;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
}

.model-info-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.6rem;
}

.model-info-title {
  font-weight: 600;
  font-size: 0.95rem;
  color: #333;
}

.model-info-close-btn {
  flex-shrink: 0;
  width: 1.4rem;
  height: 1.4rem;
  line-height: 1;
  border: none;
  border-radius: 6px;
  background: none;
  color: #666;
  cursor: pointer;
  font-size: 1rem;
}

.model-info-close-btn:hover {
  background: #eee;
}

.model-info-field {
  margin: 0 0 0.4rem;
  line-height: 1.4;
  font-size: 0.82rem;
  color: #444;
}

.model-info-description {
  margin: 0.6rem 0 0;
  line-height: 1.4;
  font-size: 0.82rem;
  color: #444;
}

.model-info-description p {
  margin: 0 0 0.4rem;
}

.model-info-description p:last-child {
  margin-bottom: 0;
}

.model-info-description ul,
.model-info-description ol {
  margin: 0 0 0.4rem;
  padding-left: 1.2rem;
}

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
