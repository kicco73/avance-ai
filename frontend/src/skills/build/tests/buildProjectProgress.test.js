// BuildProjectView.vue's Target step: a backend copy is a job of several
// steps ending in the built backend's own test run, so the panel draws
// the step table the job broadcasts instead of a spinner. Every progress
// chunk carries that table as a JSON string on `result` (see
// build/build_job.py); only the last one arrives already parsed.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

vi.mock('../api.js', () => ({
  getBuildRequirements: vi.fn().mockResolvedValue({ skills: [], required: [], disabled: [], contradicted: [] }),
  postBuildBackendCopy: vi.fn(),
  postBuildLocalModule: vi.fn()
}))

function stepTable(statuses) {
  return JSON.stringify({
    path: '/builds/proj.3/backend',
    revision: 3,
    excluded_skills: [],
    tests: null,
    steps: [
      { key: 'copy', label: 'Copying the backend', status: statuses[0] },
      { key: 'automaton', label: 'Compiling the automaton', status: statuses[1] },
      { key: 'tests', label: "Running the build's tests", status: statuses[2] }
    ]
  })
}

describe('BuildProjectView.vue build progress', () => {
  let api
  let container
  let app

  beforeEach(async () => {
    vi.resetModules()
    api = await import('../api.js')
    container = document.createElement('div')
    document.body.appendChild(container)
  })

  afterEach(() => {
    app?.unmount()
    vi.clearAllMocks()
    container.remove()
  })

  // No @vue/test-utils here, and <script setup> exposes nothing — so the
  // panel is driven the way an operator drives it: Next, then Build.
  async function build() {
    const BuildProjectView = (await import('../components/BuildProjectView.vue')).default
    app = createApp(BuildProjectView, { projectId: 'proj' })
    app.mount(container)
    await nextTick()
    container.querySelectorAll('.build-action-btn-primary')[0].click()
    await nextTick()
    container.querySelectorAll('.build-action-btn-primary')[1].click()
    await vi.waitFor(() => expect(api.postBuildBackendCopy).toHaveBeenCalled())
    await nextTick()
    await nextTick()
  }

  it('draws one row per step, marking where the build got to', async () => {
    api.postBuildBackendCopy.mockImplementation(async (projectId, excluded, onProgress) => {
      onProgress({ percentage: 33, result: stepTable(['done', 'running', 'pending']) })
      onProgress({ percentage: 100, result: stepTable(['done', 'done', 'done']) })
      return { path: '/builds/proj.3/backend', revision: 3, excluded_skills: [], tests: { passed: true, summary: '12 passed in 3.4s' } }
    })

    await build()

    const rows = container.querySelectorAll('.build-progress-step')
    expect(rows.length).toBe(3)
    expect(rows[2].textContent).toContain("Running the build's tests")
    expect(rows[2].className).toContain('build-progress-step-done')
    // The build's own test run is reported, not just that the build ended.
    expect(container.textContent).toContain('12 passed in 3.4s')
  })

  it('leaves the failed step marked as such when the build stops', async () => {
    api.postBuildBackendCopy.mockImplementation(async (projectId, excluded, onProgress) => {
      onProgress({ percentage: 66, result: stepTable(['done', 'done', 'failed']) })
      throw new Error("The build's own tests failed")
    })

    await build()

    const rows = container.querySelectorAll('.build-progress-step')
    expect(rows[2].className).toContain('build-progress-step-failed')
    expect(container.querySelector('.build-status-error').textContent).toContain("tests failed")
  })

  it('ignores a chunk whose report it cannot read, keeping what is on screen', async () => {
    api.postBuildBackendCopy.mockImplementation(async (projectId, excluded, onProgress) => {
      onProgress({ percentage: 33, result: stepTable(['done', 'running', 'pending']) })
      onProgress({ percentage: 40, result: null })
      return { path: '/x', revision: 3, excluded_skills: [], tests: null }
    })

    await build()

    const rows = container.querySelectorAll('.build-progress-step')
    expect(rows[1].className).toContain('build-progress-step-running')
  })
})
