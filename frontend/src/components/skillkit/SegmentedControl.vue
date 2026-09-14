<script setup>
defineProps({
  modelValue: { type: String, required: true },
  options: { type: Array, required: true }
})

const emit = defineEmits(['update:modelValue'])
</script>

<template>
  <div class="segmented-control">
    <button
      v-for="option in options"
      :key="option.id"
      type="button"
      class="segmented-control-option"
      :class="{ 'segmented-control-option-active': modelValue === option.id }"
      :title="option.title"
      @click="emit('update:modelValue', option.id)"
    >{{ option.label }}</button>
  </div>
</template>

<style scoped>
.segmented-control { display: flex; border: 1px solid #ddd; border-radius: 999px; overflow: hidden; background: #fff; }
.segmented-control-option { border: none; background: transparent; padding: 0.2rem 0.6rem; font: inherit; font-size: 0.7rem; color: #777; cursor: pointer; }
.segmented-control-option:hover { background: #f2f2f4; }
.segmented-control-option-active { background: #6a1b9a; color: #fff; }
.segmented-control-option-active:hover { background: #6a1b9a; }
</style>
