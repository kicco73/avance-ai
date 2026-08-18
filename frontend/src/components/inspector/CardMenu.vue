<script setup>
// The minimal top-right "⋮" contextual menu every editable detail box
// (state/action in InspectorDetailCard.vue, signal in
// InspectorSignalsTab.vue) now uses instead of its own inline "Delete"
// button — see those components' own usage. Purely presentational: what
// the menu actually does lives entirely in the slot content the caller
// provides.
import { onMounted, onUnmounted, ref } from 'vue'

const open = ref(false)
const rootEl = ref(null)

function toggle() {
  open.value = !open.value
}
function close() {
  open.value = false
}
function handleDocumentClick(event) {
  if (open.value && !rootEl.value?.contains(event.target)) close()
}

onMounted(() => document.addEventListener('click', handleDocumentClick))
onUnmounted(() => document.removeEventListener('click', handleDocumentClick))

defineExpose({ close })
</script>

<template>
  <div ref="rootEl" class="card-menu" @click.stop>
    <button type="button" class="card-menu-btn" title="More options" @click="toggle">⋮</button>
    <div v-if="open" class="card-menu-dropdown">
      <slot :close="close" />
    </div>
  </div>
</template>

<style scoped>
.card-menu { position: relative; flex-shrink: 0; }
.card-menu-btn { width: 1.4rem; height: 1.4rem; line-height: 1; border: none; border-radius: 6px; background: none; color: #666; cursor: pointer; font-size: 1rem; }
.card-menu-btn:hover { background: #eee; }
.card-menu-dropdown { position: absolute; top: calc(100% + 2px); right: 0; z-index: 20; min-width: 8rem; padding: 0.25rem; border-radius: 8px; border: 1px solid #ddd; background: white; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12); display: flex; flex-direction: column; }
.card-menu-dropdown :deep(button) { display: block; width: 100%; text-align: left; padding: 0.4rem 0.6rem; border: none; border-radius: 5px; background: none; font-size: 0.8rem; color: #333; cursor: pointer; }
.card-menu-dropdown :deep(button:hover:not(:disabled)) { background: #f0f4fa; }
.card-menu-dropdown :deep(button:disabled) { color: #aaa; cursor: not-allowed; }
.card-menu-dropdown :deep(button.card-menu-item-danger) { color: #c62828; }
.card-menu-dropdown :deep(button.card-menu-item-danger:hover:not(:disabled)) { background: #fdecea; }
</style>
