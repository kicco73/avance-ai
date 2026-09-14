<script setup>
import { inject, onMounted, ref } from 'vue'
import { getDeclarableServices } from '../../api.js'

const props = defineProps({
  services: { type: Object, default: () => ({}) },
  onSetLevel: { type: Function, required: true }
})

const SERVICE_LEVELS = [
  { id: 'required', label: 'Required', title: 'The build must include it' },
  { id: 'optional', label: 'Optional', title: 'Used if the build has it' },
  { id: 'disabled', label: 'Disabled', title: 'Never used, even when installed' }
]

const declarableServices = ref([])
const levels = ref({ ...props.services })

const closeDialog = inject('closeDialog')

onMounted(async () => {
  try {
    declarableServices.value = (await getDeclarableServices()).services
  } catch {
  }
})

function levelOf(service) {
  return levels.value[service.key] ?? 'optional'
}

function setLevel(service, level) {
  levels.value = { ...levels.value, [service.key]: level }
  props.onSetLevel(service.key, level)
}
</script>

<template>
  <div class="skills-edit-dialog">
    <h2 class="skills-edit-dialog-title">Skills</h2>
    <p class="skills-edit-dialog-hint">What this project asks of each platform skill.</p>
    <div
      v-for="service in declarableServices"
      :key="service.key"
      class="inspector-service-row"
      :title="service.ui_description"
    >
      <span class="inspector-service-name">{{ service.ui_label }}</span>
      <div class="inspector-service-levels">
        <button
          v-for="level in SERVICE_LEVELS"
          :key="level.id"
          type="button"
          class="inspector-service-level"
          :class="{ 'inspector-service-level-active': levelOf(service) === level.id }"
          :title="level.title"
          @click="setLevel(service, level.id)"
        >{{ level.label }}</button>
      </div>
    </div>
    <div class="skills-edit-dialog-actions">
      <button type="button" class="skills-edit-dialog-ok-btn" @click="closeDialog()">Done</button>
    </div>
  </div>
</template>

<style scoped>
.skills-edit-dialog { width: 100%; }
.skills-edit-dialog-title { margin: 0 0 0.3rem; padding-right: 1.6rem; font-size: 1.05rem; font-weight: 600; color: #333; }
.skills-edit-dialog-hint { margin: 0 0 0.8rem; font-size: 0.8rem; color: #777; }
.inspector-service-row { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; margin-top: 0.45rem; }
.inspector-service-name { font-size: 0.85rem; color: #444; }
.inspector-service-levels { display: flex; border: 1px solid #ddd; border-radius: 999px; overflow: hidden; background: #fff; }
.inspector-service-level { border: none; background: transparent; padding: 0.2rem 0.6rem; font: inherit; font-size: 0.7rem; color: #777; cursor: pointer; }
.inspector-service-level:hover { background: #f2f2f4; }
.inspector-service-level-active { background: #6a1b9a; color: #fff; }
.inspector-service-level-active:hover { background: #6a1b9a; }
.skills-edit-dialog-actions { display: flex; justify-content: flex-end; margin-top: 1.1rem; }
.skills-edit-dialog-ok-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #4a6fa5; background: #4a6fa5; color: white; font-size: 0.85rem; cursor: pointer; }
.skills-edit-dialog-ok-btn:hover { background: #3d5c8a; }
</style>
