<script setup>
import { computed, inject, ref, watch } from 'vue'

const props = defineProps({
  selection: { type: Object, required: true }
})

const closeDialog = inject('closeDialog')

const heading = computed(() => props.selection.heading)
const profiles = computed(() => props.selection.profiles)
const busy = computed(() => props.selection.busy)

watch(() => props.selection.open, (open) => { if (!open) closeDialog(null) })

const SLIDE_MS = 260
const SLIDE_PX = 48

const index = ref(0)
const direction = ref(1)

const current = computed(() => profiles.value[index.value])
const count = computed(() => profiles.value.length)
const canBrowse = computed(() => count.value > 1)

function show(step) {
  if (busy.value) return
  direction.value = step
  index.value = (index.value + step + count.value) % count.value
}

function select() {
  props.selection.select(current.value.name)
}

const cardEls = ref([])

function slide(el, from, to) {
  return el.animate(
    [{ opacity: from.opacity, transform: `translateX(${from.x}px)` }, { opacity: to.opacity, transform: `translateX(${to.x}px)` }],
    { duration: SLIDE_MS, easing: 'ease' }
  )
}

watch(index, (shown, hidden) => {
  const away = direction.value * SLIDE_PX
  const leaving = cardEls.value[hidden]
  const entering = cardEls.value[shown]
  leaving.classList.add('is-leaving')
  slide(leaving, { opacity: 1, x: 0 }, { opacity: 0, x: -away }).finished
    .finally(() => leaving.classList.remove('is-leaving'))
  slide(entering, { opacity: 0, x: away }, { opacity: 1, x: 0 })
})
</script>

<template>
  <div class="profile-dialog" :class="{ 'is-busy': busy }" @keydown.left="show(-1)" @keydown.right="show(1)">
    <h2 v-if="heading" class="profile-heading">{{ heading }}</h2>
    <div class="profile-stage">
      <div
        v-for="(profile, i) in profiles"
        :key="profile.name"
        ref="cardEls"
        class="profile-card"
        :class="{ 'is-current': i === index }"
      >
        <div v-if="profile.picture_url" class="profile-avatar-ring">
          <img class="profile-avatar" :src="profile.picture_url" alt="" />
        </div>
        <h3 class="profile-title">{{ profile.title }}</h3>
        <p class="profile-description">{{ profile.description }}</p>
      </div>
    </div>
    <div v-if="canBrowse" class="profile-dots" aria-hidden="true">
      <span v-for="(profile, i) in profiles" :key="profile.name" class="profile-dot" :class="{ 'is-current': i === index }"></span>
    </div>
    <div class="profile-controls">
      <button type="button" class="profile-arrow" aria-label="Previous" :disabled="!canBrowse || busy" @click="show(-1)">‹</button>
      <button type="button" class="profile-select" :disabled="!current || busy" @click="select">{{ current?.key }}</button>
      <button type="button" class="profile-arrow" aria-label="Next" :disabled="!canBrowse || busy" @click="show(1)">›</button>
    </div>
  </div>
</template>

<style scoped>
.profile-dialog {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0.9rem;
  min-width: min(340px, 78vw);
  color: #333;
}

.profile-heading {
  margin: 0;
  padding-right: 1.6rem;
  font-size: 1.05rem;
  font-weight: 600;
  color: #333;
}

.profile-stage {
  display: grid;
}

.profile-dialog.is-busy .profile-card {
  opacity: 0.55;
  transition: opacity 0.15s ease;
}

.profile-card {
  grid-area: 1 / 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 0.6rem;
  visibility: hidden;
  will-change: transform, opacity;
}

.profile-card.is-current,
.profile-card.is-leaving {
  visibility: visible;
}

.profile-avatar-ring {
  width: 8.5rem;
  height: 8.5rem;
  padding: 3px;
  border-radius: 50%;
  border: 2px solid #4a6fa5;
  background: #f5f5f7;
  box-shadow: 0 4px 16px rgba(74, 111, 165, 0.25);
}

.profile-avatar {
  display: block;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  object-fit: cover;
  background: #e3ebf7;
}

.profile-title {
  margin: 0.3rem 0 0;
  font-size: 1.25rem;
  font-weight: 600;
  color: #333;
}

.profile-description {
  margin: 0;
  max-width: 32ch;
  font-size: 0.88rem;
  line-height: 1.5;
  color: #555;
}

.profile-dots {
  display: flex;
  justify-content: center;
  gap: 0.4rem;
}

.profile-dot {
  width: 0.45rem;
  height: 0.45rem;
  border-radius: 50%;
  background: #d5dbe6;
  transition: background 0.2s ease, transform 0.2s ease;
}

.profile-dot.is-current {
  background: #4a6fa5;
  transform: scale(1.3);
}

.profile-controls {
  display: flex;
  align-items: stretch;
  gap: 0.5rem;
}

.profile-arrow {
  flex: none;
  width: 2.75rem;
  border: 1px solid #4a6fa5;
  border-radius: 6px;
  background: white;
  color: #4a6fa5;
  font-size: 1.6rem;
  line-height: 1;
  cursor: pointer;
}

.profile-arrow:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.profile-select {
  flex: 1 1 auto;
  padding: 0.6rem 1rem;
  border: 1px solid #4a6fa5;
  border-radius: 6px;
  background: #4a6fa5;
  color: white;
  font-size: 0.95rem;
  font-weight: 600;
  cursor: pointer;
}

.profile-select:hover:not(:disabled) {
  background: #3d5c8a;
}

.profile-select:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@media (hover: hover) {
  .profile-arrow:hover:not(:disabled) {
    background: #4a6fa5;
    color: white;
  }
}

@media (hover: none) and (pointer: coarse) {
  .profile-arrow,
  .profile-select {
    min-height: 2.75rem;
  }
}
</style>
