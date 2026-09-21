<script setup>
import { computed, ref, watch } from 'vue'
import { appStoreFileContentUrl } from '../../api.js'

const props = defineProps({
  appId: { type: String, required: true },
  snapshotFiles: { type: Object, default: () => ({}) }
})

const ASPECT_LABELS = {
  'web': 'Web',
  'mobile-vertical': 'Mobile vertical',
  'mobile-horizontal': 'Mobile horizontal'
}

const ASPECT_ORDER = ['web', 'mobile-vertical', 'mobile-horizontal']

function isCoarsePointer() {
  return typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches === true
}

function isPortrait() {
  return typeof window !== 'undefined' && window.matchMedia?.('(orientation: portrait)').matches === true
}

function preferredAspect(available) {
  if (!isCoarsePointer()) return available.includes('web') ? 'web' : available[0]
  const wanted = isPortrait() ? 'mobile-vertical' : 'mobile-horizontal'
  return available.includes(wanted) ? wanted : available[0]
}

const aspects = computed(() => {
  const present = Object.keys(props.snapshotFiles).filter((aspect) => props.snapshotFiles[aspect]?.length)
  return [...present].sort((a, b) => {
    const ai = ASPECT_ORDER.indexOf(a)
    const bi = ASPECT_ORDER.indexOf(b)
    return (ai === -1 ? ASPECT_ORDER.length : ai) - (bi === -1 ? ASPECT_ORDER.length : bi)
  })
})

const selectedAspect = ref(null)
const selectedIndex = ref(0)

watch(aspects, (list) => {
  if (!list.length) {
    selectedAspect.value = null
    return
  }
  if (!list.includes(selectedAspect.value)) selectedAspect.value = preferredAspect(list)
}, { immediate: true })

watch(selectedAspect, () => { selectedIndex.value = 0 })

const files = computed(() => (selectedAspect.value ? props.snapshotFiles[selectedAspect.value] ?? [] : []))
const currentFile = computed(() => files.value[selectedIndex.value] ?? null)

function aspectLabel(aspect) {
  return ASPECT_LABELS[aspect] ?? aspect
}

function snapshotUrl(fileName) {
  return appStoreFileContentUrl(props.appId, fileName)
}

defineExpose({ hasSnapshots: computed(() => aspects.value.length > 0) })
</script>

<template>
  <div class="app-snapshot-gallery">
    <div class="app-snapshot-gallery-bar">
      <div class="app-snapshot-gallery-aspects">
        <button
          v-for="aspect in aspects"
          :key="aspect"
          type="button"
          class="app-snapshot-gallery-aspect"
          :class="{ 'app-snapshot-gallery-aspect-active': aspect === selectedAspect }"
          @click="selectedAspect = aspect"
        >{{ aspectLabel(aspect) }}</button>
      </div>
      <span class="app-snapshot-gallery-count">{{ files.length }} snapshot{{ files.length === 1 ? '' : 's' }}</span>
    </div>

    <div class="app-snapshot-gallery-stage">
      <img v-if="currentFile" :src="snapshotUrl(currentFile)" class="app-snapshot-gallery-image" alt="" />
    </div>

    <div v-if="files.length > 1" class="app-snapshot-gallery-thumbs">
      <button
        v-for="(file, index) in files"
        :key="file"
        type="button"
        class="app-snapshot-gallery-thumb"
        :class="{ 'app-snapshot-gallery-thumb-active': index === selectedIndex }"
        :aria-label="`Snapshot ${index + 1}`"
        @click="selectedIndex = index"
      >
        <img :src="snapshotUrl(file)" alt="" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.app-snapshot-gallery {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.app-snapshot-gallery-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.app-snapshot-gallery-aspects {
  display: inline-flex;
  gap: 2px;
  padding: 3px;
  background: #f2f2f7;
  border-radius: 8px;
}

.app-snapshot-gallery-aspect {
  padding: 0.3rem 0.75rem;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #777;
  font-size: 0.78rem;
  font-weight: 600;
  cursor: pointer;
}

.app-snapshot-gallery-aspect-active {
  background: white;
  color: #333;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.14);
}

.app-snapshot-gallery-count {
  color: #999;
  font-size: 0.72rem;
}

.app-snapshot-gallery-stage {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 0.9rem;
  box-sizing: border-box;
  background: #f2f2f7;
  border: 1px solid #e5e5ea;
  border-radius: 8px;
}

.app-snapshot-gallery-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: 6px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.1);
}

.app-snapshot-gallery-thumbs {
  flex-shrink: 0;
  display: flex;
  gap: 0.5rem;
  overflow-x: auto;
}

.app-snapshot-gallery-thumb {
  flex-shrink: 0;
  width: 64px;
  height: 48px;
  padding: 0;
  border: 1px solid #ddd;
  border-radius: 5px;
  background: white;
  overflow: hidden;
  cursor: pointer;
}

.app-snapshot-gallery-thumb-active {
  border: 2px solid #4a6fa5;
}

.app-snapshot-gallery-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
</style>
