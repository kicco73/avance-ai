<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'

const props = defineProps({
  files: { type: Array, default: () => [] },
  filesLoading: { type: Boolean, default: true },
  currentFileName: { type: String, default: null },
  uploading: { type: Boolean, default: false },
  creatingFile: { type: Boolean, default: false },
  explorerWidth: { type: Number, required: true }
})

const emit = defineEmits(['new-attachment', 'new-aspect', 'new-legal', 'select-file', 'upload-file'])

const fileInputRef = ref(null)

function triggerUpload() {
  fileInputRef.value?.click()
}

// "+" menu — same toggle/click-outside pattern as SettingsMenu.vue.
const newFileMenuOpen = ref(false)
const newFileMenuRootEl = ref(null)

function toggleNewFileMenu() {
  newFileMenuOpen.value = !newFileMenuOpen.value
}

function selectNewAttachment() {
  newFileMenuOpen.value = false
  emit('new-attachment')
}

function selectNewAspect() {
  newFileMenuOpen.value = false
  emit('new-aspect')
}

function selectNewLegal() {
  newFileMenuOpen.value = false
  emit('new-legal')
}

function handleClickOutsideNewFileMenu(event) {
  if (newFileMenuOpen.value && newFileMenuRootEl.value && !newFileMenuRootEl.value.contains(event.target)) {
    newFileMenuOpen.value = false
  }
}

document.addEventListener('click', handleClickOutsideNewFileMenu, true)

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutsideNewFileMenu, true)
})

const ASPECT_PREFIX = 'aspect/'
const BEHAVIOUR_PREFIX = 'behaviour/'
const LEGAL_TERMS_FILE_NAME = 'legal/terms.md'

function basename(name) {
  const idx = name.lastIndexOf('/')
  return idx === -1 ? name : name.slice(idx + 1)
}

const themeAssets = computed(() => props.files.filter((name) => name.startsWith(ASPECT_PREFIX)))
const behaviorAttachments = computed(() => props.files.filter((name) => name.startsWith(BEHAVIOUR_PREFIX)))
const hasIndexCss = computed(() => props.files.includes('index.css'))
const showThemeBranch = computed(() => hasIndexCss.value || themeAssets.value.length > 0)
const hasLegalTerms = computed(() => props.files.includes(LEGAL_TERMS_FILE_NAME))

// Every branch starts closed on entry — the watch below reopens
// whichever one holds a file a jump-to-definition/attachment click opens.
const expanded = ref({ behavior: false, theme: false })
function toggleBranch(key) {
  expanded.value[key] = !expanded.value[key]
}

// Reveals whichever branch holds the file a jump-to-definition or attachment
// click just opened, even if that branch is currently collapsed.
watch(
  () => props.currentFileName,
  (name) => {
    if (name === 'index.css' || themeAssets.value.includes(name)) expanded.value.theme = true
    else if (name === 'index.yml' || behaviorAttachments.value.includes(name)) expanded.value.behavior = true
  }
)
</script>

<template>
  <div class="file-explorer" :style="{ width: explorerWidth + 'px' }">
    <div class="file-explorer-header">
      <span class="file-explorer-title">Explorer</span>
      <div class="file-explorer-header-actions">
        <button class="file-explorer-icon-btn" :disabled="uploading" title="Upload files" @click="triggerUpload">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
            <path d="M12 3l4 4h-3v6h-2V7H8l4-4zM5 19v-6h2v6h10v-6h2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z" />
          </svg>
        </button>
        <div class="file-explorer-new-menu" ref="newFileMenuRootEl">
          <button class="file-explorer-icon-btn" :disabled="creatingFile" title="New file" @click="toggleNewFileMenu">+</button>
          <ul v-if="newFileMenuOpen" class="file-explorer-new-menu-list">
            <li><button class="file-explorer-new-menu-item" @click="selectNewAttachment">New attachment</button></li>
            <li><button class="file-explorer-new-menu-item" :disabled="hasIndexCss" :title="hasIndexCss ? 'index.css already exists' : ''" @click="selectNewAspect">New aspect</button></li>
            <li><button class="file-explorer-new-menu-item" :disabled="hasLegalTerms" :title="hasLegalTerms ? 'legal/terms.md already exists' : ''" @click="selectNewLegal">New legal</button></li>
          </ul>
        </div>
      </div>
      <input
        ref="fileInputRef"
        type="file"
        multiple
        accept=".txt,.md,.csv,.yml,.yaml,.css,.png,.jpg,.jpeg,.gif,.webp,.svg"
        class="file-explorer-upload-input"
        @change="emit('upload-file', $event)"
      />
    </div>
    <p v-if="filesLoading" class="file-explorer-status">Loading…</p>
    <ul v-else class="file-explorer-tree">
      <li class="file-explorer-branch">
        <div class="file-explorer-node-row">
          <button class="file-explorer-caret" :class="{ 'file-explorer-caret-open': expanded.behavior }" title="Toggle" @click="toggleBranch('behavior')">▸</button>
          <button
            class="file-explorer-item"
            :class="{ 'file-explorer-item-active': currentFileName === 'index.yml' }"
            title="index.yml"
            @click="emit('select-file', 'index.yml')"
          >
            Behavior
          </button>
        </div>
        <div class="file-explorer-children-wrap" :class="{ 'file-explorer-children-wrap-open': expanded.behavior }">
          <ul class="file-explorer-children">
            <li v-if="behaviorAttachments.length === 0" class="file-explorer-empty">No attachments</li>
            <li v-for="name in behaviorAttachments" :key="name" class="file-explorer-row">
              <span class="file-explorer-ai-icon" title="Read by the AI">
                <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor"><path d="M19 9l1.25-2.75L23 5l-2.75-1.25L19 1l-1.25 2.75L15 5l2.75 1.25L19 9zM11.5 9.5L9 4 6.5 9.5 1 12l5.5 2.5L9 20l2.5-5.5L17 12l-5.5-2.5zM19 15l-1.25 2.75L15 19l2.75 1.25L19 23l1.25-2.75L23 19l-2.75-1.25L19 15z"/></svg>
              </span>
              <button
                class="file-explorer-item file-explorer-item-child"
                :class="{ 'file-explorer-item-active': name === currentFileName }"
                :title="basename(name)"
                @click="emit('select-file', name)"
              >
                {{ basename(name) }}
              </button>
            </li>
          </ul>
        </div>
      </li>

      <li v-if="showThemeBranch" class="file-explorer-branch">
        <div class="file-explorer-node-row">
          <button class="file-explorer-caret" :class="{ 'file-explorer-caret-open': expanded.theme }" title="Toggle" @click="toggleBranch('theme')">▸</button>
          <button
            class="file-explorer-item"
            :class="{ 'file-explorer-item-active': currentFileName === 'index.css' }"
            title="index.css"
            @click="emit('select-file', 'index.css')"
          >
            Aspect
          </button>
        </div>
        <div class="file-explorer-children-wrap" :class="{ 'file-explorer-children-wrap-open': expanded.theme }">
          <ul class="file-explorer-children">
            <li v-if="themeAssets.length === 0" class="file-explorer-empty">No assets</li>
            <li v-for="name in themeAssets" :key="name" class="file-explorer-row">
              <button
                class="file-explorer-item file-explorer-item-child"
                :class="{ 'file-explorer-item-active': name === currentFileName }"
                :title="basename(name)"
                @click="emit('select-file', name)"
              >
                {{ basename(name) }}
              </button>
            </li>
          </ul>
        </div>
      </li>

      <li v-if="hasLegalTerms" class="file-explorer-branch">
        <div class="file-explorer-node-row">
          <span class="file-explorer-caret-spacer"></span>
          <button
            class="file-explorer-item"
            :class="{ 'file-explorer-item-active': currentFileName === LEGAL_TERMS_FILE_NAME }"
            title="legal/terms.md"
            @click="emit('select-file', LEGAL_TERMS_FILE_NAME)"
          >
            Legal
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.file-explorer { flex-shrink: 0; display: flex; flex-direction: column; min-width: 0; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }
.file-explorer-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0.4rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #ddd; background: #f7f8fa; }
.file-explorer-header-actions { display: flex; gap: 0.4rem; }
.file-explorer-title { font-size: 0.8rem; font-weight: 600; color: #555; text-transform: uppercase; letter-spacing: 0.03em; }
.file-explorer-icon-btn { display: flex; align-items: center; justify-content: center; width: 1.6rem; height: 1.6rem; border-radius: 6px; border: 1px solid #4a6fa5; background: white; color: #4a6fa5; cursor: pointer; padding: 0; font-size: 0.9rem; line-height: 1; }
.file-explorer-icon-btn:hover:not(:disabled) { background: #4a6fa5; color: white; }
.file-explorer-icon-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.file-explorer-upload-input { display: none; }
.file-explorer-new-menu { position: relative; }
.file-explorer-new-menu-list {
  position: absolute;
  top: calc(100% + 0.3rem);
  right: 0;
  min-width: 160px;
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 100;
}
.file-explorer-new-menu-item { display: block; width: 100%; text-align: left; padding: 0.5rem 0.9rem; border: none; background: none; cursor: pointer; font-size: 0.85rem; color: #4a6fa5; }
.file-explorer-new-menu-item:hover:not(:disabled) { background: #f0f4fa; }
.file-explorer-new-menu-item:disabled { color: #999; cursor: not-allowed; }
.file-explorer-status { margin: 0; padding: 0.6rem; font-size: 0.85rem; color: #444; }
.file-explorer-tree { list-style: none; margin: 0; padding: 0.3rem; overflow-y: auto; flex: 1; }
.file-explorer-branch + .file-explorer-branch { margin-top: 0.2rem; }
.file-explorer-node-row { display: flex; align-items: center; gap: 0.1rem; }
.file-explorer-caret { flex-shrink: 0; width: 1.2rem; height: 1.6rem; display: flex; align-items: center; justify-content: center; border: none; background: none; cursor: pointer; font-size: 0.7rem; color: #777; padding: 0; transform: rotate(0deg); transition: transform 0.18s ease; }
.file-explorer-caret-open { transform: rotate(90deg); }
.file-explorer-caret-spacer { flex-shrink: 0; width: 1.2rem; }
.file-explorer-children-wrap { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.18s ease; }
.file-explorer-children-wrap-open { grid-template-rows: 1fr; }
.file-explorer-children { list-style: none; margin: 0; padding: 0 0 0 1.2rem; overflow: hidden; min-height: 0; }
.file-explorer-empty { padding: 0.3rem 0.5rem; font-size: 0.78rem; color: #999; font-style: italic; }
.file-explorer-row { display: flex; align-items: center; gap: 0.2rem; }
/* Same "read by the AI" sparkle as MdEditorPanel.vue/InspectorDetailCard.vue
   — a Behavior attachment is exactly that: content the AI reads. */
.file-explorer-ai-icon { display: inline-flex; flex-shrink: 0; color: #8b5cf6; margin-left: 0.3rem; }
.file-explorer-item { flex: 1; min-width: 0; display: block; text-align: left; padding: 0.4rem 0.5rem; border: none; border-radius: 6px; background: none; cursor: pointer; font-size: 0.85rem; color: #333; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.file-explorer-item-child { font-size: 0.82rem; color: #555; }
.file-explorer-item:hover { background: #f0f4fa; }
.file-explorer-item-active { background: #e4ecf9; color: #2c4d7a; font-weight: 600; }
</style>
