<script setup>
import { ref } from 'vue'
import { fieldLabel } from './serviceFields.js'

defineProps({
  name: { type: String, required: true },
  value: { type: [String, Number], default: '' }
})

const revealed = ref(false)
</script>

<template>
  <div class="services-field">
    <label class="services-field-label">{{ fieldLabel(name) }}</label>
    <div class="services-field-masked-row">
      <input class="services-field-input" :type="revealed ? 'text' : 'password'" :value="value" disabled />
      <button
        type="button"
        class="services-reveal-btn"
        :title="revealed ? 'Hide' : 'Show'"
        @click="revealed = !revealed"
      >{{ revealed ? 'Hide' : 'Show' }}</button>
    </div>
  </div>
</template>

<style scoped>
.services-field { display: flex; flex-direction: column; gap: 0.3rem; margin-bottom: 0.9rem; }
.services-field-label { font-size: 0.78rem; color: #666; text-transform: uppercase; letter-spacing: 0.03em; }
.services-field-input { padding: 0.45rem 0.6rem; border: 1px solid #ddd; border-radius: 6px; background: #fafafa; color: #333; font-size: 0.9rem; flex: 1; min-width: 0; }
.services-field-masked-row { display: flex; align-items: center; gap: 0.5rem; }
.services-reveal-btn { flex-shrink: 0; padding: 0.4rem 0.7rem; border: 1px solid #ddd; border-radius: 6px; background: white; color: #4a6fa5; cursor: pointer; font-size: 0.8rem; }
.services-reveal-btn:hover { background: #f2f5f9; }
</style>
