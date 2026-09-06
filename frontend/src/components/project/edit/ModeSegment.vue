<script setup>
defineProps({
  mode: { type: String, required: true }
})

const emit = defineEmits(['update:mode'])

const MODES = [
  { id: 'edit', label: 'Design' },
  { id: 'run', label: 'Run' },
  { id: 'test', label: 'Test' }
]
</script>

<template>
  <div class="mode-segment">
    <button
      v-for="option in MODES"
      :key="option.id"
      class="mode-segment-btn"
      :class="{ 'mode-segment-btn-active': mode === option.id }"
      @click="emit('update:mode', option.id)"
    >{{ option.label }}</button>
  </div>
</template>

<style scoped>
.mode-segment {
  display: flex;
  gap: 0.2rem;
  padding: 0.2rem;
  border-radius: 8px;
  background: #eef1f5;
}

.mode-segment-btn {
  padding: 0.4rem 1rem;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
  font-size: 0.88rem;
  color: #555;
}

.mode-segment-btn:hover {
  color: #333;
}

.mode-segment-btn-active {
  background: white;
  color: #2c4d7a;
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12);
}
</style>
