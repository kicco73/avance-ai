<script setup>
import { inject, onMounted, ref } from 'vue'
import { getDeclarableServices } from '../../api.js'
import SegmentedControl from '../../../../components/skillkit/SegmentedControl.vue'

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
      <SegmentedControl
        :model-value="levelOf(service)"
        :options="SERVICE_LEVELS"
        @update:model-value="(level) => setLevel(service, level)"
      />
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
.skills-edit-dialog-actions { display: flex; justify-content: flex-end; margin-top: 1.1rem; }
.skills-edit-dialog-ok-btn { padding: 0.4rem 1rem; border-radius: 6px; border: 1px solid #4a6fa5; background: #4a6fa5; color: white; font-size: 0.85rem; cursor: pointer; }
.skills-edit-dialog-ok-btn:hover { background: #3d5c8a; }
</style>
