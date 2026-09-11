<script setup>
// The "Build" button's own wizard (see ProjectDetailPanel.vue). The
// Compile step is a placeholder — compiling a project's published revision
// into a local module now happens as the second half of the "Publish"
// button next to this one (see ProjectDetailPanel.vue's own @publish, and
// useProjectAdminActions.js's handlePublishProject), not through this
// wizard.
import { computed, onMounted, ref } from 'vue'
import AppHeader from '../../AppHeader.vue'
import ProfileMenu from '../../ProfileMenu.vue'
import { getBuildSkills, postBuildBackendCopy } from '../../../api/build.js'

const props = defineProps({
  projectId: { type: String, required: true },
  profile: { type: Object, default: null }
})

const emit = defineEmits(['close', 'home', 'profile', 'logout'])

const STEPS = [
  { id: 'skills', label: 'Skills' },
  { id: 'target', label: 'Target' }
]

// One mark per step status the job reports (see build/build_job.py).
const STEP_MARKS = {
  pending: '○',
  running: '◐',
  done: '●',
  failed: '✕'
}

const currentStep = ref(0)
const building = ref(false)
const buildResult = ref(null)
const buildError = ref('')
// What the build is doing right now, as the job reports it: one entry
// per step, each with its own status. Comes back on every progress chunk
// (see build/build_job.py's own `result`), so the panel shows where a
// build got to rather than that it is still going.
const buildSteps = ref([])
const buildPercentage = ref(0)

const testReport = computed(() => buildResult.value?.tests ?? null)

function onBuildProgress(message) {
  buildPercentage.value = message.percentage ?? 0
  // The job's own report travels as a JSON string on every chunk; only
  // the last one is parsed for us (see readSseResult).
  try {
    const report = typeof message.result === 'string' ? JSON.parse(message.result) : message.result
    if (report?.steps) buildSteps.value = report.steps
  } catch {
    // A chunk without a readable report changes nothing about what is
    // already on screen.
  }
}

// What this backend has installed, as the server reads it off its own
// source tree — never a list kept here. A skill switched off is a
// directory the build does not copy, so what is off is what is absent.
const skills = ref([])
const included = ref({})
// The packages this project actually uses — what it declared as
// `required` plus what its own automaton calls into, worked out on the
// server: ticked and not untickable, since a build without one produces
// a server that fails where the automaton expects the call to work.
const required = ref([])
// The ones it declared `disabled` (project.services — see
// PROJECT_SPECS.md §1.2): left out by default and marked as refused,
// though an operator may still include them — a disabled service simply
// goes unused, it does not have to be absent.
const disabled = ref([])
// The few it declared disabled while still calling into them. Not a
// refusal to build: the call bounces at run time exactly as it would in
// a build without the package, which is worth saying here rather than
// leaving to a log.
const contradicted = ref([])
const skillsError = ref('')

const excludedSkills = computed(() => skills.value.map(s => s.package).filter(p => !included.value[p]))

function isRequired(skill) {
  return required.value.includes(skill.package)
}

function isDisabled(skill) {
  return disabled.value.includes(skill.package)
}

const contradictedLabels = computed(() => skills.value
  .filter(skill => contradicted.value.includes(skill.package))
  .map(skill => skill.ui_label))

onMounted(async () => {
  try {
    const { skills: installed, required: mandatory, disabled: refused, contradicted: conflicting }
      = await getBuildSkills(props.projectId)
    skills.value = installed
    required.value = mandatory
    disabled.value = refused ?? []
    contradicted.value = conflicting ?? []
    // Off unless the project actually uses it: the smallest build that
    // still runs this project is the starting point, and anything else
    // is something the operator asks for on purpose.
    included.value = Object.fromEntries(installed.map(s => [s.package, mandatory.includes(s.package)]))
  } catch (err) {
    skillsError.value = err.message || 'Could not read the installed skills.'
  }
})

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
  buildSteps.value = []
  buildPercentage.value = 0
  try {
    buildResult.value = await postBuildBackendCopy(props.projectId, excludedSkills.value, onBuildProgress)
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
        <p class="build-status">
          What this build includes. A skill left out is not switched off in the result — its code
          is not there at all.
        </p>
        <p v-if="contradictedLabels.length" class="build-status build-status-warning">
          This project calls into {{ contradictedLabels.join(', ') }} but declares it disabled — those calls
          will come back undelivered, whether or not the build includes it.
        </p>
        <p v-if="skillsError" class="build-status build-status-error">{{ skillsError }}</p>
        <p v-else-if="!skills.length" class="build-status">No optional skills installed.</p>
        <ul v-else class="build-skill-list">
          <li v-for="skill in skills" :key="skill.package" class="build-skill-row">
            <label class="build-skill-label" :class="{ 'build-skill-label-required': isRequired(skill) }">
              <input
                v-model="included[skill.package]"
                type="checkbox"
                class="build-skill-toggle"
                :disabled="isRequired(skill)"
              >
              <span class="build-skill-text">
                <span class="build-skill-name">
                  {{ skill.ui_label }}
                  <span v-if="isRequired(skill)" class="build-skill-required">this project uses it</span>
                  <span v-else-if="isDisabled(skill)" class="build-skill-disabled">this project disabled it</span>
                </span>
                <span v-if="skill.ui_description" class="build-skill-description">{{ skill.ui_description }}</span>
              </span>
            </label>
            <code class="build-skill-package">src/{{ skill.package }}</code>
          </li>
        </ul>
        <div class="build-actions-row">
          <button type="button" class="build-action-btn build-action-btn-primary" @click="goNext">Next</button>
        </div>
      </div>

      <div v-show="currentStep === 1" class="build-panel">
        <p v-if="!buildSteps.length && !buildResult && !buildError" class="build-status">
          Copies this backend, with the project's compiled automaton and its own database
          (every other project removed from it), into a standalone directory under
          <code>builds/</code> — then runs that backend's own test suite, which is only the
          tests of the skills this build kept.
        </p>

        <ol v-if="buildSteps.length" class="build-progress">
          <li
            v-for="step in buildSteps"
            :key="step.key"
            class="build-progress-step"
            :class="`build-progress-step-${step.status}`"
          >
            <span class="build-progress-mark" aria-hidden="true">{{ STEP_MARKS[step.status] }}</span>
            <span class="build-progress-label">{{ step.label }}</span>
          </li>
        </ol>
        <div v-if="building" class="build-progress-bar">
          <span class="build-progress-bar-fill" :style="{ width: `${buildPercentage}%` }" />
        </div>

        <p v-if="buildResult" class="build-status">
          Built revision {{ buildResult.revision }} into {{ buildResult.path }}.
          <template v-if="buildResult.excluded_skills?.length">
            Left out: {{ buildResult.excluded_skills.join(', ') }}.
          </template>
        </p>
        <p v-if="testReport" class="build-status">
          Its own tests: {{ testReport.summary }}
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

.build-skill-list {
  list-style: none;
  margin: 0 0 1rem;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.build-skill-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid #dde3ec;
  border-radius: 6px;
}

.build-skill-label {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  cursor: pointer;
}

.build-skill-text {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.build-skill-name {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.build-skill-description {
  font-size: 0.78rem;
  color: #7b8794;
  line-height: 1.35;
}

.build-skill-toggle {
  width: 1rem;
  height: 1rem;
  margin-top: 0.15rem;
  cursor: pointer;
}

.build-skill-label-required {
  cursor: default;
}

.build-skill-required {
  font-size: 0.72rem;
  color: #777;
  border: 1px solid #ddd;
  border-radius: 999px;
  padding: 0.05rem 0.4rem;
}

.build-skill-disabled {
  font-size: 0.72rem;
  color: #a1701a;
  border: 1px solid #e8d4a6;
  background: #fdf6e6;
  border-radius: 999px;
  padding: 0.05rem 0.4rem;
}

.build-skill-package {
  color: #7b8794;
  font-size: 0.85em;
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

.build-progress {
  list-style: none;
  margin: 0 0 0.75rem;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.build-progress-step {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.9rem;
  color: #999;
}

.build-progress-step-done {
  color: #4a6fa5;
}

.build-progress-step-running {
  color: #2c4d7a;
  font-weight: 600;
}

.build-progress-step-failed {
  color: #b23a3a;
  font-weight: 600;
}

.build-progress-mark {
  width: 1rem;
  text-align: center;
}

.build-progress-bar {
  height: 4px;
  border-radius: 2px;
  background: #f0f0f2;
  overflow: hidden;
  margin-bottom: 0.5rem;
}

.build-progress-bar-fill {
  display: block;
  height: 100%;
  background: #4a6fa5;
  transition: width 0.25s ease;
}

.build-status-warning {
  color: #a1701a;
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
