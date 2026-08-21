<script setup>
// Read-only detail card for whichever file is currently open in the
// design-mode file explorer (an attachment, an asset, or index.css itself
// — never index.yml, which has no delete and its own Actions/Signals tabs
// instead). Same badge/title/CardMenu convention as InspectorProjectCard.vue
// and InspectorDetailCard.vue, minus any edit form: there's nothing on a
// plain file worth editing here beyond deleting it.
import { computed } from 'vue'
import CardMenu from './CardMenu.vue'

const props = defineProps({
  fileName: { type: String, required: true },
  deleting: { type: Boolean, default: false }
})

const emit = defineEmits(['delete'])

const TYPE_LABELS = {
  '.yml': 'YAML',
  '.yaml': 'YAML',
  '.txt': 'Text',
  '.md': 'Markdown',
  '.csv': 'CSV',
  '.css': 'Stylesheet',
  '.png': 'PNG image',
  '.jpg': 'JPEG image',
  '.jpeg': 'JPEG image',
  '.gif': 'GIF image',
  '.webp': 'WebP image',
  '.svg': 'SVG image'
}

const fileType = computed(() => {
  const dot = props.fileName.lastIndexOf('.')
  const ext = dot === -1 ? '' : props.fileName.slice(dot).toLowerCase()
  return TYPE_LABELS[ext] ?? 'File'
})

function handleDelete() {
  emit('delete')
}
</script>

<template>
  <div class="inspector-detail-card inspector-file-card">
    <div class="inspector-detail-header">
      <div class="inspector-detail-header-top">
        <span class="inspector-detail-badge inspector-detail-badge-file">File</span>
        <span class="inspector-detail-title" :title="fileName">{{ fileName }}</span>
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
      <p class="inspector-detail-field"><strong>Type:</strong> {{ fileType }}</p>
    </div>
  </div>
</template>

<style scoped>
.inspector-file-card { cursor: default; }
.inspector-detail-card { flex-shrink: 0; margin-top: 0.75rem; max-height: 45%; display: flex; flex-direction: column; border-radius: 8px; border: 1px solid #eee; background: #fafafa; overflow: hidden; }
.inspector-detail-header { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; flex-shrink: 0; }
.inspector-detail-header-top { display: flex; align-items: center; gap: 0.5rem; }
.inspector-detail-badge { flex-shrink: 0; font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; padding: 0.15rem 0.5rem; border-radius: 999px; color: white; }
.inspector-detail-badge-file { background: #37474f; }
.inspector-detail-title { flex: 1; min-width: 0; font-weight: 600; font-size: 0.85rem; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inspector-detail-body { padding: 0.6rem 0.75rem; font-size: 0.8rem; color: #444; }
.inspector-detail-field { margin: 0; line-height: 1.4; }
</style>
