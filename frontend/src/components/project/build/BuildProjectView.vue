<script setup>
// The "Build" button's own wizard (see ProjectDetailPanel.vue). Step 1
// (Compile) is still a placeholder; step 2 (Target) picks where the
// output goes and, for "Local module", actually builds it — the only
// target with a backend behind it so far (see build_controller.py). The
// target buttons stay selectable but do not change what Build does yet.
import { ref } from 'vue'
import AppHeader from '../../AppHeader.vue'
import ProfileMenu from '../../ProfileMenu.vue'
import { postBuildLocalModule } from '../../../api/build.js'

const props = defineProps({
  projectId: { type: String, required: true },
  profile: { type: Object, default: null }
})

const emit = defineEmits(['close', 'home', 'profile', 'logout'])

const STEPS = [
  { id: 'compile', label: 'Compile' },
  { id: 'target', label: 'Target' }
]

const TARGET_OPTIONS = [
  { id: 'zip', label: 'Zip file' },
  { id: 'repository', label: 'Push to repository' },
  { id: 'module', label: 'Local module' }
]

const currentStep = ref(0)
const targetOption = ref(null)
const repositoryName = ref('')
const building = ref(false)
const buildResult = ref(null)
const buildError = ref('')

async function build() {
  building.value = true
  buildResult.value = null
  buildError.value = ''
  try {
    buildResult.value = await postBuildLocalModule(props.projectId)
  } catch (error) {
    buildError.value = error?.message || 'Build failed.'
  } finally {
    building.value = false
  }
}

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
        <label class="build-field-label">Target</label>
        <div class="build-target-options">
          <button
            v-for="option in TARGET_OPTIONS"
            :key="option.id"
            type="button"
            class="build-target-option"
            :class="{ 'build-target-option-active': targetOption === option.id }"
            @click="targetOption = option.id"
          >{{ option.label }}</button>
        </div>

        <template v-if="targetOption === 'repository'">
          <label class="build-field-label">Repository name</label>
          <input
            class="build-field-input"
            type="text"
            v-model="repositoryName"
            placeholder="org/repo"
          />
        </template>

        <p v-if="buildResult" class="build-status build-status-ok">
          Built <code>{{ buildResult.module }}</code> from revision {{ buildResult.revision }}.
        </p>
        <p v-else-if="buildError" class="build-status build-status-error">{{ buildError }}</p>

        <div class="build-actions-row">
          <button type="button" class="build-action-btn" @click="goBack">Back</button>
          <button
            type="button"
            class="build-action-btn build-action-btn-primary"
            :disabled="building"
            @click="build"
          >{{ building ? 'Building…' : 'Build' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.build-status-ok {
  color: #1a7f37;
}

.build-status-error {
  color: #b42318;
}

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

.build-field-label {
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: #777;
}

.build-field-input {
  width: 100%;
  box-sizing: border-box;
  padding: 0.4rem 0.6rem;
  border: 1px solid #ddd;
  border-radius: 6px;
  background: white;
  color: #333;
  font: inherit;
  font-size: 0.85rem;
}

.build-status {
  margin: 0;
  font-size: 0.9rem;
  color: #666;
}

.build-target-options {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}

.build-target-option {
  padding: 0.55rem 0.8rem;
  border: 1px solid #ddd;
  border-radius: 6px;
  background: white;
  color: #333;
  text-align: left;
  cursor: pointer;
  font-size: 0.85rem;
}

.build-target-option:hover {
  border-color: #4a6fa5;
}

.build-target-option-active {
  border-color: #4a6fa5;
  background: #eef3fa;
  color: #2c4d7a;
  font-weight: 600;
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
