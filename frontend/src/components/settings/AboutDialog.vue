<script setup>
// Settings menu's "About Avance..." dialog — name/version straight off
// the running backend (main.py's own __version__), fetched fresh on
// every open rather than cached, so it always reflects whatever build is
// actually serving the request.
import { onMounted, ref } from 'vue'
import { getAbout } from '../../api.js'

const emit = defineEmits(['close'])

const name = ref('')
const version = ref('')
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const about = await getAbout()
    name.value = about.name
    version.value = about.version
  } catch {
    // already surfaced via apiFetch
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="about-dialog-overlay" @click.self="emit('close')">
    <div class="about-dialog">
      <p v-if="loading" class="about-dialog-status">Loading…</p>
      <template v-else>
        <h2 class="about-dialog-name">{{ name }}</h2>
        <p class="about-dialog-version">Version {{ version }}</p>
      </template>
      <div class="about-dialog-actions">
        <button class="about-dialog-close-btn" @click="emit('close')">Close</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.about-dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
}

.about-dialog {
  background: white;
  border-radius: 10px;
  padding: 1.5rem;
  min-width: 240px;
  max-width: 360px;
  text-align: center;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
}

.about-dialog-status {
  margin: 0;
  font-size: 0.9rem;
  color: #777;
}

.about-dialog-name {
  margin: 0 0 0.4rem;
  font-size: 1.2rem;
  font-weight: 600;
  color: #4a6fa5;
  letter-spacing: 0.02em;
}

.about-dialog-version {
  margin: 0;
  font-size: 0.85rem;
  color: #666;
}

.about-dialog-actions {
  display: flex;
  justify-content: center;
  margin-top: 1.2rem;
}

.about-dialog-close-btn {
  padding: 0.4rem 1.2rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.85rem;
}

.about-dialog-close-btn:hover {
  background: #4a6fa5;
  color: white;
}
</style>
