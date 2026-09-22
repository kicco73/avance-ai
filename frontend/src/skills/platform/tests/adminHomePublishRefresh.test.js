import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const backend = { revision: 3, published: 2, compiled: false }

function listedApp() {
  return {
    id: 'proj', ui_label: 'Proj', ui_description: 'the proj one', icon_file: null, family: 'demo',
    reactions_enabled: false, compiled: backend.compiled, is_paused: false, installed: false, ai_summary: null,
  }
}

vi.mock('../api.js', async (importOriginal) => ({
  ...(await importOriginal()),
  getState: () => Promise.resolve({}),
  activateProject: () => Promise.resolve({}),
  getProjectFiles: () => Promise.resolve({ files: [] }),
  getProjectMetadata: (id) => Promise.resolve({ project: { ui_label: id, ui_description: `the ${id} one`, family: 'demo' } }),
  getProjectsRuntimeStatus: () => Promise.resolve({ projects: [
    { id: 'proj', status: 'running', revision: backend.revision, published_revision: backend.published, broken: { published: null, draft: null } },
  ] }),
  getManagedProjectApps: () => Promise.resolve({ apps: [listedApp()] }),
  projectFileContentUrl: () => '',
  getPublishPreview: () => Promise.resolve({ needs_remap: false, has_active_sessions: false }),
  postPublishProject: () => {
    backend.published = backend.revision
    backend.compiled = true
    return Promise.resolve({ revision: backend.revision, published_revision: backend.published, built: { module: 'proj.3' } })
  },
}))
vi.mock('../../../dialogStore.js', () => ({
  aboutDialog: vi.fn(),
  confirmDialog: vi.fn().mockResolvedValue(true),
  customDialog: vi.fn(),
  infoDialog: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('../../../chatStore.js', () => ({
  handleStateChange: vi.fn(),
  loadMessages: vi.fn(),
  clearChatUi: vi.fn(),
}))

describe('publishing from the manage projects panel', () => {
  let container
  let app

  beforeEach(async () => {
    backend.revision = 3
    backend.published = 2
    backend.compiled = false
    container = document.createElement('div')
    document.body.appendChild(container)
    const { default: AdminHome } = await import('../components/AdminHome.vue')
    app = createApp(AdminHome, { profile: null, viewStack: { pushView: vi.fn() } })
    app.mount(container)
    await settle()
  }, 30000)

  afterEach(() => {
    app.unmount()
    container.remove()
  })

  async function settle() {
    for (let i = 0; i < 40; i++) await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
    for (let i = 0; i < 40; i++) await nextTick()
  }

  function panel() {
    return container.querySelector('.manage-projects-preview')
  }

  function button(label) {
    return [...panel().querySelectorAll('button')].find((b) => b.textContent.trim() === label)
  }

  function badges() {
    return [...panel().querySelectorAll('.project-detail-badge')].map((b) => b.textContent)
  }

  it('shows the published state as soon as the publish is reported', async () => {
    const row = [...container.querySelectorAll('.manage-projects-row')]
      .find((el) => el.querySelector('.project-card-title').textContent === 'proj')
    row.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    expect(panel().querySelector('.project-detail-rev').textContent).toBe('rev. 2')
    expect(badges()).not.toContain('COMPILED')
    expect(button('Publish')).toBeDefined()
    expect(button('Build').disabled).toBe(true)

    button('Publish').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    await settle()

    expect(panel().querySelector('.project-detail-rev').textContent).toBe('rev. 3')
    expect(badges()).toContain('COMPILED')
    expect(button('Publish')).toBeUndefined()
    expect(button('Build').disabled).toBe(false)
  })

  it('is effectively single-root, so a class pushed onto it by App.vue\'s Transition (the slide animation) actually lands on the overlay', async () => {
    app.unmount()
    const { default: AdminHome } = await import('../components/AdminHome.vue')
    app = createApp({
      setup: () => () => h(AdminHome, { class: 'view-pushed', profile: null, viewStack: { pushView: vi.fn() } })
    })
    app.mount(container)
    await settle()

    expect(container.children.length).toBe(1)
    const overlay = container.querySelector('.manage-projects-overlay')
    expect(overlay).not.toBeNull()
    expect(overlay.classList.contains('view-pushed')).toBe(true)
  })

  it('offers to publish a compiled app whose draft has moved past the published revision', async () => {
    app.unmount()
    backend.compiled = true
    const { default: AdminHome } = await import('../components/AdminHome.vue')
    app = createApp(AdminHome, { profile: null, viewStack: { pushView: vi.fn() } })
    app.mount(container)
    await settle()

    const row = [...container.querySelectorAll('.manage-projects-row')]
      .find((el) => el.querySelector('.project-card-title').textContent === 'proj')
    row.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    expect(badges()).toContain('COMPILED')
    expect(button('Publish')).toBeDefined()
  })
})
