import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const APPS = [
  { id: 'first', ui_label: 'First', ui_description: 'the first one' },
  { id: 'second', ui_label: 'Second', ui_description: 'the second one' },
]

const ROWS = [
  { id: 'first', status: 'running', revision: 3, published_revision: 2 },
  { id: 'second', status: 'running', revision: 1, published_revision: 1 },
]

vi.mock('../src/skills/platform/api.js', async (importOriginal) => ({
  ...(await importOriginal()),
  getAppStoreApps: () => Promise.resolve({ apps: APPS }),
  getProjectFiles: () => Promise.resolve({ files: [] }),
  getProjectMetadata: (id) => Promise.resolve({ project: { ui_label: id, family: 'demo' } }),
  getProjectsRuntimeStatus: () => Promise.resolve({ projects: ROWS }),
  projectFileContentUrl: () => '',
}))

describe('the manage projects preview column', () => {
  let container
  let app
  let warnings

  beforeEach(async () => {
    container = document.createElement('div')
    document.body.appendChild(container)
    const { default: ManageProjectsView } = await import('../src/skills/platform/components/settings/ManageProjectsView.vue')
    warnings = []
    app = createApp(ManageProjectsView)
    app.config.warnHandler = (message) => warnings.push(message)
    app.mount(container)
    await settle()
  })

  afterEach(() => {
    app.unmount()
    container.remove()
  })

  async function settle() {
    for (let i = 0; i < 30; i++) await nextTick()
  }

  async function selectRow(index) {
    container.querySelectorAll('.manage-projects-row')[index].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
  }

  function preview() {
    return container.querySelector('.manage-projects-preview')
  }

  it('shows the detail panel of the project whose card was selected', async () => {
    await selectRow(0)
    expect(preview().querySelector('.project-detail-title').textContent).toBe('First')

    await selectRow(1)
    expect(preview().querySelector('.project-detail-title').textContent).toBe('Second')
  })

  it('leaves nothing behind that could cover the panel', async () => {
    await selectRow(0)
    await selectRow(1)
    expect(preview().children.length).toBe(1)
    expect(preview().children[0].className).toBe('manage-projects-detail')
  })

  it('renders the panel without a Vue warning', async () => {
    await selectRow(0)
    expect(warnings).toEqual([])
  })
})
