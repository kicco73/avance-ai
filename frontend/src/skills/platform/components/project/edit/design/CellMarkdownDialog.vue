<script setup>
import { inject, onBeforeUnmount, onMounted, ref } from 'vue'
import { createCrepe } from '../../../../markdownCrepe.js'

const props = defineProps({
  title: { type: String, required: true },
  initialValue: { type: String, default: '' }
})

const closeDialog = inject('closeDialog')

const editorHost = ref(null)
const content = ref(props.initialValue)

let crepe = null

onMounted(async () => {
  crepe = createCrepe({ root: editorHost.value, defaultValue: props.initialValue })
  crepe.on((listener) => {
    listener.markdownUpdated((_ctx, markdown) => { content.value = markdown })
  })
  await crepe.create()
  content.value = crepe.getMarkdown()
})

onBeforeUnmount(() => {
  crepe?.destroy()
  crepe = null
})

function confirm() {
  closeDialog(content.value.trimEnd())
}
</script>

<template>
  <h2 class="cell-md-title">{{ title }}</h2>
  <div ref="editorHost" class="cell-md-host"></div>
  <div class="cell-md-actions">
    <button class="cell-md-cancel-btn" @click="closeDialog(null)">Cancel</button>
    <button class="cell-md-ok-btn" @mousedown.prevent @click="confirm">OK</button>
  </div>
</template>

<style scoped>
.cell-md-title { margin: 0 0 0.6rem; padding-right: 1.6rem; font-size: 1.05rem; font-weight: 600; color: #333; }
.cell-md-host { height: 60vh; overflow: auto; border: 1px solid #ddd; border-radius: 6px; }
.cell-md-host :deep(.milkdown) { min-height: 100%; --crepe-base-font-size: 15px; --crepe-font-default: inherit; --crepe-font-title: inherit; }
.cell-md-host :deep(.milkdown .ProseMirror) { padding: 0.75rem 1.5rem 3rem; min-height: 100%; }
.cell-md-host :deep(.milkdown span[data-type="hardbreak"][data-is-inline="true"]) { display: block; height: 0; line-height: 0; }
.cell-md-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 1rem; }
.cell-md-ok-btn { padding: 0.4rem 0.9rem; border-radius: 6px; border: 1px solid #4a6fa5; background: #4a6fa5; color: white; cursor: pointer; font-size: 0.85rem; }
.cell-md-cancel-btn { padding: 0.4rem 0.9rem; border-radius: 6px; border: 1px solid #ccc; background: white; color: #444; cursor: pointer; font-size: 0.85rem; }
</style>
