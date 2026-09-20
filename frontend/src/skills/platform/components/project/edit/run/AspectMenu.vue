<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { ASPECTS, aspectFor } from './aspects.js'

const props = defineProps({
  modelValue: { type: String, required: true }
})

const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const rootEl = ref(null)

const current = computed(() => aspectFor(props.modelValue))

function toggle() {
  open.value = !open.value
}

function close() {
  open.value = false
}

function select(id) {
  if (id !== props.modelValue) emit('update:modelValue', id)
  close()
}

function handleClickOutside(event) {
  if (!open.value) return
  if (rootEl.value?.contains(event.target)) return
  close()
}
document.addEventListener('click', handleClickOutside, true)
onBeforeUnmount(() => document.removeEventListener('click', handleClickOutside, true))
</script>

<template>
  <div class="aspect-menu" ref="rootEl">
    <button type="button" class="aspect-menu-btn" :title="`Aspect: ${current.label}`" @click="toggle">
      <span class="aspect-menu-btn-label">Aspect: {{ current.label }}</span>
      <span class="aspect-menu-btn-caret">▾</span>
    </button>
    <div v-if="open" class="aspect-menu-panel">
      <ul class="aspect-menu-list">
        <li v-for="a in ASPECTS" :key="a.id">
          <button type="button" class="aspect-menu-item" @click="select(a.id)">
            <span class="aspect-menu-item-check">{{ a.id === modelValue ? '✓' : '' }}</span>
            <span class="aspect-menu-item-label">{{ a.label }}</span>
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.aspect-menu { position: relative; display: flex; align-items: center; flex-shrink: 0; }

.aspect-menu-btn {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.35rem 0.6rem;
  border: 1px solid #4a6fa5;
  border-radius: 6px;
  background: white;
  color: #4a6fa5;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
}
.aspect-menu-btn:hover { background: #4a6fa5; color: white; }
.aspect-menu-btn-caret { font-size: 0.65rem; }

.aspect-menu-panel {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  min-width: 180px;
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  z-index: 1000;
  overflow: hidden;
}

.aspect-menu-list { list-style: none; margin: 0; padding: 0.3rem 0; }

.aspect-menu-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  width: 100%;
  padding: 0.5rem 0.8rem;
  border: none;
  background: none;
  font-size: 0.85rem;
  color: #333;
  text-align: left;
  cursor: pointer;
}
.aspect-menu-item:hover { background: #f0f4fa; }

.aspect-menu-item-check { flex: none; display: inline-block; width: 1rem; color: #2e7d32; font-weight: 600; }
</style>
