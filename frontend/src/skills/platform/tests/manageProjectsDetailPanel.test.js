import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const ROWS = [
  { id: 'first', status: 'running', revision: 3, published_revision: 2 },
  { id: 'second', status: 'running', revision: 1, published_revision: 1 },
  { id: 'rotten', status: 'paused', revision: 4, published_revision: 4, broken: { published: 'index.yml: bad yaml' } },
  { id: 'fresh', status: 'running', revision: 1, published_revision: null },
]

vi.mock('../api.js', async (importOriginal) => ({
  ...(await importOriginal()),
  getProjectFiles: () => Promise.resolve({ files: [] }),
  getProjectMetadata: (id) => (id === 'rotten'
    ? Promise.reject(new Error('index.yml: bad yaml'))
    : Promise.resolve({ project: { ui_label: id, ui_description: `the ${id} one`, family: 'demo' } })),
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
    const { default: ManageProjectsView } = await import('../components/settings/ManageProjectsView.vue')
    warnings = []
    app = createApp(ManageProjectsView)
    app.config.warnHandler = (message) => warnings.push(message)
    app.mount(container)
    await settle()
  }, 30000)

  afterEach(() => {
    app.unmount()
    container.remove()
  })

  async function settle() {
    for (let i = 0; i < 30; i++) await nextTick()
  }

  async function selectProject(id) {
    const row = [...container.querySelectorAll('.manage-projects-row')]
      .find((el) => el.querySelector('.project-card-title').textContent === id)
    row.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
  }

  function preview() {
    return container.querySelector('.manage-projects-preview')
  }

  it('shows the detail panel of the project whose card was selected', async () => {
    await selectProject('first')
    expect(preview().querySelector('.project-detail-title').textContent).toBe('first')

    await selectProject('second')
    expect(preview().querySelector('.project-detail-title').textContent).toBe('second')
  })

  it('leaves nothing behind that could cover the panel', async () => {
    await selectProject('first')
    await selectProject('second')
    expect(preview().children.length).toBe(1)
    expect(preview().children[0].className).toBe('manage-projects-detail')
  })

  it('keeps showing a panel for a project whose published revision no longer builds', async () => {
    await selectProject('rotten')
    expect(preview().querySelector('.project-detail-title').textContent).toBe('rotten')
    expect(preview().textContent).toContain('index.yml: bad yaml')
  })

  it('keeps showing a panel for a project that has never been published', async () => {
    await selectProject('fresh')
    expect(preview().querySelector('.project-detail-title').textContent).toBe('fresh')
    expect(preview().textContent).toContain("hasn't been published yet")
  })

  it('offers Edit and Export on a project that cannot be tried', async () => {
    await selectProject('rotten')
    const labels = [...preview().querySelectorAll('.project-detail-secondary-btn')].map((b) => b.textContent)
    expect(labels).toContain('Edit')
    expect(labels).toContain('Export')
  })

  it('shows the project label and description the list shows', async () => {
    await selectProject('first')
    expect(preview().querySelector('.project-detail-desc').textContent).toBe('the first one')
  })

  it('disables only the Test button on a project that cannot be tried', async () => {
    await selectProject('rotten')
    expect(preview().querySelector('.project-detail-try-btn').disabled).toBe(true)
    expect([...preview().querySelectorAll('.project-detail-secondary-btn')].some((b) => b.disabled)).toBe(false)

    await selectProject('first')
    expect(preview().querySelector('.project-detail-try-btn').disabled).toBe(false)
  })

  it('renders the panel without a Vue warning', async () => {
    await selectProject('first')
    expect(warnings).toEqual([])
  })
})
