<script setup>
import { computed, nextTick, ref, watch } from 'vue'
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

const DRAG_THRESHOLD_PX = 40

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
const trackEl = ref(null)

watch(aspects, (list) => {
  if (!list.length) {
    selectedAspect.value = null
    return
  }
  if (!list.includes(selectedAspect.value)) selectedAspect.value = preferredAspect(list)
}, { immediate: true })

watch(selectedAspect, () => {
  selectedIndex.value = 0
  nextTick(() => { if (trackEl.value) trackEl.value.scrollLeft = 0 })
})

const files = computed(() => (selectedAspect.value ? props.snapshotFiles[selectedAspect.value] ?? [] : []))

function aspectLabel(aspect) {
  return ASPECT_LABELS[aspect] ?? aspect
}

function snapshotUrl(fileName) {
  return appStoreFileContentUrl(props.appId, fileName)
}

function goTo(index) {
  const track = trackEl.value
  const clamped = Math.min(Math.max(0, index), files.value.length - 1)
  selectedIndex.value = clamped
  if (track) track.scrollTo({ left: clamped * track.clientWidth, behavior: 'smooth' })
}

const dragging = ref(false)
let dragPointerId = null
let dragStartX = 0
let dragStartScrollLeft = 0
let dragMoved = false

function onPointerDown(event) {
  const track = trackEl.value
  if (!track || files.value.length < 2 || event.button === 2) return
  dragging.value = true
  dragMoved = false
  dragPointerId = event.pointerId
  dragStartX = event.clientX
  dragStartScrollLeft = track.scrollLeft
  track.setPointerCapture?.(event.pointerId)
}

function onPointerMove(event) {
  if (!dragging.value || event.pointerId !== dragPointerId) return
  const track = trackEl.value
  if (!track) return
  const delta = event.clientX - dragStartX
  if (Math.abs(delta) > 3) dragMoved = true
  track.scrollLeft = dragStartScrollLeft - delta
}

function endDrag(event) {
  if (!dragging.value || (event && event.pointerId !== dragPointerId)) return
  const track = trackEl.value
  dragging.value = false
  dragPointerId = null
  if (!track) return
  track.releasePointerCapture?.(event?.pointerId)
  const travelled = track.scrollLeft - dragStartScrollLeft
  const from = Math.round(dragStartScrollLeft / track.clientWidth)
  if (!dragMoved) return goTo(from)
  if (Math.abs(travelled) < DRAG_THRESHOLD_PX) return goTo(from)
  goTo(from + (travelled > 0 ? 1 : -1))
}

function onKeydown(event) {
  if (event.key === 'ArrowRight') {
    event.preventDefault()
    goTo(selectedIndex.value + 1)
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault()
    goTo(selectedIndex.value - 1)
  }
}
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
    </div>

    <div
      ref="trackEl"
      class="app-snapshot-gallery-track"
      :class="{ 'app-snapshot-gallery-track-dragging': dragging, 'app-snapshot-gallery-track-single': files.length < 2 }"
      role="group"
      :aria-label="`Snapshot ${selectedIndex + 1} of ${files.length}`"
      tabindex="0"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="endDrag"
      @pointercancel="endDrag"
      @keydown="onKeydown"
      @dragstart.prevent
    >
      <div v-for="file in files" :key="file" class="app-snapshot-gallery-slide">
        <img :src="snapshotUrl(file)" class="app-snapshot-gallery-image" alt="" draggable="false" />
      </div>
    </div>

    <div v-if="files.length > 1" class="app-snapshot-gallery-dots">
      <button
        v-for="(file, index) in files"
        :key="file"
        type="button"
        class="app-snapshot-gallery-dot"
        :class="{ 'app-snapshot-gallery-dot-active': index === selectedIndex }"
        :aria-label="`Snapshot ${index + 1}`"
        :aria-current="index === selectedIndex ? 'true' : undefined"
        @click="goTo(index)"
      ></button>
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

.app-snapshot-gallery-track {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow-x: auto;
  overflow-y: hidden;
  scroll-snap-type: x mandatory;
  scrollbar-width: none;
  background: #f2f2f7;
  border: 1px solid #e5e5ea;
  border-radius: 8px;
  cursor: grab;
  touch-action: pan-y;
  outline-offset: 2px;
}

.app-snapshot-gallery-track::-webkit-scrollbar {
  display: none;
}

.app-snapshot-gallery-track-dragging {
  cursor: grabbing;
  scroll-snap-type: none;
}

.app-snapshot-gallery-track-single {
  cursor: default;
}

.app-snapshot-gallery-slide {
  flex: 0 0 100%;
  min-width: 0;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 0.9rem;
  box-sizing: border-box;
  scroll-snap-align: center;
}

.app-snapshot-gallery-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: 6px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.1);
  user-select: none;
  -webkit-user-drag: none;
}

.app-snapshot-gallery-dots {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.1rem;
}

/* A 8px dot with a 24px hit area: the visible mark is the ::before,
   the button itself is what a finger has to land on. */
.app-snapshot-gallery-dot {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
}

.app-snapshot-gallery-dot::before {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c8ccd4;
  transition: background 0.15s ease, transform 0.15s ease;
}

.app-snapshot-gallery-dot-active::before {
  background: #4a6fa5;
  transform: scale(1.25);
}
</style>
