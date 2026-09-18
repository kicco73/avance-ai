<script setup>
import { computed, onMounted, ref } from 'vue'
import VuePdfEmbed from 'vue-pdf-embed'
import { renderMarkdown } from '../markdown.js'
import { mediaKindFromUrl } from '../mediaKind.js'

const props = defineProps({
  url: { type: String, required: true }
})

const kind = computed(() => mediaKindFromUrl(props.url))
const markdownHtml = ref('')
const markdownLoading = ref(false)

onMounted(async () => {
  if (kind.value !== 'markdown') return
  markdownLoading.value = true
  try {
    const response = await fetch(props.url, { credentials: 'include' })
    markdownHtml.value = renderMarkdown(await response.text())
  } catch {
  } finally {
    markdownLoading.value = false
  }
})
</script>

<template>
  <div class="media-dialog">
    <VuePdfEmbed v-if="kind === 'pdf'" :key="url" :source="url" />
    <p v-else-if="kind === 'markdown' && markdownLoading" class="media-dialog-status">Loading…</p>
    <div v-else-if="kind === 'markdown'" class="media-dialog-markdown" v-html="markdownHtml"></div>
    <img v-else :key="url" :src="url" class="media-dialog-image" />
  </div>
</template>

<style scoped>
.media-dialog { max-height: 80vh; overflow: auto; }
.media-dialog-status { margin: 0; padding: 1rem 0; color: #444; }
.media-dialog-image { display: block; max-width: 100%; max-height: 80vh; margin: 0 auto; border-radius: 6px; }
.media-dialog-markdown { text-align: left; }
.media-dialog-markdown :deep(p) { margin: 0 0 0.8rem; }
.media-dialog-markdown :deep(p:last-child) { margin-bottom: 0; }
.media-dialog-markdown :deep(img) { max-width: 100%; border-radius: 6px; }
</style>
