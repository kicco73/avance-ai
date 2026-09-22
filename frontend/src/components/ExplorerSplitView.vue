<script setup>
import { useResizablePanel } from '../composables/useResizablePanel.js'

const props = defineProps({
  items: { type: Array, required: true },
  activeId: { type: String, default: null },
  title: { type: String, default: 'Explorer' },
  initialWidth: { type: Number, default: 200 },
  minWidth: { type: Number, default: 140 },
  maxWidth: { type: Number, default: 360 }
})

const emit = defineEmits(['select'])

const { width, startDrag } = useResizablePanel(props.initialWidth, { min: props.minWidth, max: props.maxWidth })
</script>

<template>
  <div class="explorer-split-view">
    <div class="split-explorer" :style="{ width: width + 'px' }">
      <div class="split-explorer-header">
        <span class="split-explorer-title">{{ title }}</span>
      </div>
      <ul class="split-explorer-tree">
        <li v-for="item in items" :key="item.id" class="split-explorer-row">
          <button
            class="split-explorer-item"
            :class="{ 'split-explorer-item-active': item.id === activeId }"
            @click="emit('select', item.id)"
          >
            <slot name="item-icon" :item="item" />
            {{ item.label }}
          </button>
        </li>
      </ul>
    </div>

    <div class="split-divider" @mousedown="startDrag"></div>

    <div class="split-content-pane">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.explorer-split-view { flex: 1; display: flex; min-height: 0; }

.split-explorer { flex-shrink: 0; display: flex; flex-direction: column; min-width: 0; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }
.split-explorer-header { display: flex; align-items: center; padding: 0.5rem 0.6rem; border-bottom: 1px solid #ddd; background: #f7f8fa; }
.split-explorer-title { font-size: 0.8rem; font-weight: 600; color: #555; text-transform: uppercase; letter-spacing: 0.03em; }
.split-explorer-tree { list-style: none; margin: 0; padding: 0.3rem; overflow-y: auto; flex: 1; }
.split-explorer-item { flex: 1; min-width: 0; display: flex; align-items: center; gap: 0.35rem; width: 100%; text-align: left; padding: 0.4rem 0.5rem; border: none; border-radius: 6px; background: none; cursor: pointer; font-size: 0.85rem; color: #333; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.split-explorer-item:hover { background: #f0f4fa; }
.split-explorer-item-active { background: #e4ecf9; color: #2c4d7a; font-weight: 600; }

.split-divider { flex-shrink: 0; width: 6px; margin: 0 0.4rem; border-radius: 3px; background: transparent; cursor: col-resize; }
.split-divider:hover { background: #dbe4f0; }

.split-content-pane { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; border: 1px solid #ddd; border-radius: 8px; overflow: hidden; }
</style>
