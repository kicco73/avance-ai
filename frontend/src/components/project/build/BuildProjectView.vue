<script setup>
// The "Build" button's own wizard (see ProjectDetailPanel.vue). The
// Compile step is a placeholder — compiling a project's published revision into
// a local module now happens directly from the "Compile" button next to
// this one (see ProjectDetailPanel.vue's own @compile, and
// useProjectAdminActions.js's handleCompileProject), not through this
// wizard.
import { ref } from 'vue'
import AppHeader from '../../AppHeader.vue'
import ProfileMenu from '../../ProfileMenu.vue'
import { postBuildBackendCopy } from '../../../api/build.js'

const props = defineProps({
  projectId: { type: String, required: true },
  profile: { type: Object, default: null }
})

const emit = defineEmits(['close', 'home', 'profile', 'logout'])

const STEPS = [
  { id: 'skills', label: 'Skills' },
  { id: 'target', label: 'Target' }
]

const currentStep = ref(0)
const building = ref(false)
const buildResult = ref(null)
const buildError = ref('')

function goToStep(index) {
  if (index > currentStep.value + 1) return
  currentStep.value = index
}

function goNext() {
  if (currentStep.value < STEPS.length - 1) currentStep.value += 1
}

function goBack() {
  if (currentStep.value > 0) currentStep.value -= 1
}

async function runBuild() {
  building.value = true
  buildResult.value = null
  buildError.value = ''
  try {
    buildResult.value = await postBuildBackendCopy(props.projectId)
  } catch (err) {
    buildError.value = err.message || 'Build failed.'
  } finally {
    building.value = false
  }
}
</script>

<template>
  <div class="build-overlay">
    <AppHeader>
      <template #left>
        <button class="app-header-icon-btn" title="Back" @click="emit('close')">«</button>
      </template>
      <template #center>
        <h2 class="app-header-title build-header-title">Build — {{ projectId }}</h2>
      </template>
      <template #right>
        <ProfileMenu :profile="profile" @home="emit('home')" @profile="emit('profile')" @logout="emit('logout')" />
      </template>
    </AppHeader>

    <div class="build-steps">
      <template v-for="(step, index) in STEPS" :key="step.id">
        <button
          type="button"
          class="build-step-btn"
          :class="{ 'build-step-btn-active': currentStep === index, 'build-step-btn-done': currentStep > index }"
          :disabled="index > currentStep + 1"
          @click="goToStep(index)"
        >
          <span class="build-step-index">{{ index + 1 }}</span>
          {{ step.label }}
        </button>
        <span v-if="index < STEPS.length - 1" class="build-step-sep" />
      </template>
    </div>

    <div class="build-body">
      <div v-show="currentStep === 0" class="build-panel">
        <p class="build-status">Nothing here yet.</p>
        <div class="build-actions-row">
          <button type="button" class="build-action-btn build-action-btn-primary" @click="goNext">Next</button>
        </div>
      </div>

      <div v-show="currentStep === 1" class="build-panel">
        <p v-if="!buildResult && !buildError" class="build-status">
          Copies this backend, with the project's compiled automaton and its own database
          (every other project removed from it), into a standalone directory under
          <code>builds/</code> — then launches it once to confirm it actually starts.
        </p>
        <p v-if="buildResult" class="build-status">
          Built revision {{ buildResult.revision }} into {{ buildResult.path }} — launched and confirmed working.
        </p>
        <p v-if="buildError" class="build-status build-status-error">{{ buildError }}</p>
        <div class="build-actions-row">
          <button type="button" class="build-action-btn" :disabled="building" @click="goBack">Back</button>
          <button
            type="button"
            class="build-action-btn build-action-btn-primary"
            :disabled="building"
            @click="runBuild"
          >
            {{ building ? 'Building…' : 'Build' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.build-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: calc(-1 * var(--viewport-bottom-overshoot, 0px));
  box-sizing: border-box;
  padding-left: var(--safe-area-left);
  padding-right: var(--safe-area-right);
  background: white;
  z-index: 100;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
}

.build-header-title {
  color: #4a6fa5;
}

.build-steps {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.9rem 1.25rem;
  border-bottom: 1px solid #ddd;
  flex-shrink: 0;
}

.build-step-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.3rem 0.7rem;
  border: none;
  border-radius: 999px;
  background: none;
  cursor: pointer;
  font-size: 0.85rem;
  color: #999;
}

.build-step-btn:disabled {
  cursor: default;
}

.build-step-btn-active {
  color: #2c4d7a;
  font-weight: 600;
}

.build-step-btn-done {
  color: #4a6fa5;
}

.build-step-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.3rem;
  height: 1.3rem;
  border-radius: 50%;
  background: #f0f0f2;
  font-size: 0.75rem;
}

.build-step-btn-active .build-step-index,
.build-step-btn-done .build-step-index {
  background: #4a6fa5;
  color: white;
}

.build-step-sep {
  width: 1.5rem;
  height: 1px;
  background: #ddd;
}

.build-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 1.25rem;
  padding-bottom: calc(1.25rem + var(--safe-area-bottom));
}

.build-panel {
  max-width: 420px;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.build-status {
  margin: 0;
  font-size: 0.9rem;
  color: #666;
}

.build-status-error {
  color: #b23a3a;
}

.build-actions-row {
  display: flex;
  gap: 0.6rem;
  margin-top: 1rem;
}

.build-action-btn {
  display: inline-flex;
  align-items: center;
  padding: 0.4rem 1rem;
  border-radius: 6px;
  border: 1px solid #4a6fa5;
  background: white;
  color: #4a6fa5;
  cursor: pointer;
  font-size: 0.85rem;
}

.build-action-btn:hover {
  background: #4a6fa5;
  color: white;
}

.build-action-btn:disabled {
  opacity: 0.5;
  cursor: default;
  background: white;
  color: #4a6fa5;
}

.build-action-btn-primary {
  background: #4a6fa5;
  color: white;
}

.build-action-btn-primary:hover {
  background: #395680;
}
</style>
