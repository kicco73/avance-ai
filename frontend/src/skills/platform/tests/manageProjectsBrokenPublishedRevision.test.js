import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick } from 'vue'

globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function managedApp() {
  return {
    id: 'rotten', ui_label: 'Rotten', ui_description: 'published revision no longer builds',
    icon_file: null, family: null, reactions_enabled: false, compiled: false,
    is_paused: true, installed: false, ai_summary: null,
  }
}

vi.mock('../api.js', async (importOriginal) => ({
  ...(await importOriginal()),
  getState: () => Promise.resolve({}),
  activateProject: () => Promise.resolve({}),
  getProjectFiles: () => Promise.resolve({ files: [] }),
  getProjectMetadata: (id) => Promise.resolve({ project: { ui_label: 'Rotten', ui_description: `the ${id} one`, family: 'demo' } }),
  getProjectsRuntimeStatus: () => Promise.resolve({ projects: [
    {
      id: 'rotten', status: 'paused', paused_reason: 'published revision no longer builds',
      revision: 7, published_revision: 6,
      broken: { published: 'boom', draft: null },
    },
  ] }),
  getManagedProjectApps: () => Promise.resolve({ apps: [managedApp()] }),
  getAppStoreApps: () => Promise.resolve({ apps: [] }),
  projectFileContentUrl: () => '',
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

describe('a project stopped because its published revision is broken', () => {
  let container
  let app

  beforeEach(async () => {
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

  it('still shows its detail panel, so the fixed draft can be published', async () => {
    const row = [...container.querySelectorAll('.manage-projects-row')]
      .find((el) => el.querySelector('.project-card-title').textContent === 'Rotten')
    row.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()

    expect(panel().textContent).not.toContain("hasn't been published yet")
    expect(panel().querySelector('.project-detail-title').textContent).toBe('Rotten')
    expect(panel().querySelector('.project-detail-rev').textContent).toBe('rev. 6')
    expect(button('Publish')).toBeDefined()
    expect(button('Edit')).toBeDefined()
  })
})
