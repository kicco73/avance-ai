<script setup>
// A "(?)" button that opens a dialog rendering one of backend/src/docs'
// fixed reference docs (see api.js's getDoc/controller.py's DOC_FILES) —
// reused wherever a view wants to link to its own piece of documentation
// (EditProjectView.vue's own, next to Save; the Inspector's Metrics/
// Performance tabs) instead of duplicating the content into the
// frontend bundle. Fetched lazily, once, the first time it's opened —
// never on mount, since most of these dialogs are never opened at all
// in a given session.
import { ref } from 'vue'
import { getDoc } from '../api.js'
import { renderMarkdown } from '../markdown.js'

const props = defineProps({
  docName: { type: String, required: true },
  title: { type: String, default: 'Documentation' }
})

const open = ref(false)
const loading = ref(false)
const content = ref('')
const loaded = ref(false)

async function show() {
  open.value = true
  if (loaded.value) return
  loading.value = true
  try {
    const result = await getDoc(props.docName)
    content.value = result.content
    loaded.value = true
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <button class="doc-info-btn" :title="`About ${title}`" @click="show">?</button>

  <Teleport to="body">
    <div v-if="open" class="doc-info-overlay" @click.self="open = false">
      <div class="doc-info-dialog">
        <div class="doc-info-header">
          <span class="doc-info-title">{{ title }}</span>
          <button class="doc-info-close-btn" title="Close" @click="open = false">×</button>
        </div>
        <p v-if="loading" class="doc-info-status">Loading…</p>
        <div v-else class="doc-info-content" v-html="renderMarkdown(content)"></div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.doc-info-btn {
  flex-shrink: 0;
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

.doc-info-btn:hover {
  background: #4a6fa5;
  color: white;
}
</style>

<style>
/* Unscoped: the dialog lives under <body> via Teleport, outside this
   component's normal DOM subtree, so a scoped [data-v-xxx] attribute
   selector would never match it. */
.doc-info-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 1001;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}

.doc-info-dialog {
  background: white;
  border-radius: 10px;
  padding: 1rem 1.4rem;
  max-width: 760px;
  width: 100%;
  max-height: 100%;
  overflow-y: auto;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
}

.doc-info-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.6rem;
  position: sticky;
  top: 0;
  background: white;
  padding-bottom: 0.4rem;
  border-bottom: 1px solid #eee;
}

.doc-info-title {
  font-weight: 600;
  font-size: 0.95rem;
  color: #333;
}

.doc-info-close-btn {
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

.doc-info-close-btn:hover {
  background: #eee;
}

.doc-info-status {
  margin: 0;
  color: #444;
  font-size: 0.9rem;
}

.doc-info-content {
  font-size: 0.85rem;
  line-height: 1.55;
  color: #333;
}

.doc-info-content :is(h1, h2, h3, h4) {
  margin: 1rem 0 0.4rem;
  line-height: 1.3;
}

.doc-info-content h1 { font-size: 1.15rem; }
.doc-info-content h2 { font-size: 1.05rem; }
.doc-info-content h3 { font-size: 0.95rem; }

.doc-info-content p {
  margin: 0 0 0.6rem;
}

.doc-info-content ul,
.doc-info-content ol {
  margin: 0 0 0.6rem;
  padding-left: 1.3rem;
}

.doc-info-content code {
  background: #f2f2f2;
  border-radius: 3px;
  padding: 0.1rem 0.3rem;
  font-size: 0.85em;
}

.doc-info-content pre {
  background: #f7f7f7;
  border: 1px solid #eee;
  border-radius: 6px;
  padding: 0.6rem 0.8rem;
  overflow-x: auto;
  margin: 0 0 0.6rem;
}

.doc-info-content pre code {
  background: none;
  padding: 0;
}

.doc-info-content table {
  border-collapse: collapse;
  margin: 0 0 0.6rem;
  font-size: 0.82rem;
}

.doc-info-content th,
.doc-info-content td {
  border: 1px solid #ddd;
  padding: 0.3rem 0.5rem;
  text-align: left;
}
</style>
