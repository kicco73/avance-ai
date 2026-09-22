import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'

const { getAppStoreApps } = vi.hoisted(() => ({ getAppStoreApps: vi.fn() }))

vi.mock('../api.js', () => ({
  getAppStoreApps,
  appStoreFileContentUrl: () => '',
}))

const AppStoreView = (await import('../components/appStore/AppStoreView.vue')).default

async function mount(role) {
  getAppStoreApps.mockResolvedValue({ apps: [] })
  const host = document.createElement('div')
  document.body.appendChild(host)
  const emitted = {}
  const app = createApp({
    setup: () => () => h(AppStoreView, {
      role,
      standalone: true,
      onManageProjects: () => { emitted.manageProjects = (emitted.manageProjects ?? 0) + 1 },
      onManageUsers: () => { emitted.manageUsers = (emitted.manageUsers ?? 0) + 1 },
      onManageServices: () => { emitted.manageServices = (emitted.manageServices ?? 0) + 1 },
    })
  })
  app.mount(host)
  await nextTick()
  await nextTick()
  return { host, emitted }
}

describe('the Settings menu on the shared app-store home', () => {
  afterEach(() => {
    getAppStoreApps.mockReset()
  })

  it('shows Settings for an admin', async () => {
    const { host } = await mount('admin')

    expect(host.querySelector('.settings-btn')).not.toBeNull()
  })

  it('hides Settings for a customer', async () => {
    const { host } = await mount('customer')

    expect(host.querySelector('.settings-btn')).toBeNull()
  })

  it('hides Settings for a supervisor', async () => {
    const { host } = await mount('supervisor')

    expect(host.querySelector('.settings-btn')).toBeNull()
  })

  it('no longer lists App store as a Settings item — it is the always-visible Store button instead', async () => {
    const { host } = await mount('admin')

    host.querySelector('.settings-btn').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()

    const labels = [...host.querySelectorAll('.settings-item')].map((b) => b.textContent.trim())
    expect(labels).toEqual(['Manage projects', 'Manage users', 'Manage services'])
  })

  it('forwards Manage projects/users/services clicks up to the caller', async () => {
    const { host, emitted } = await mount('admin')

    host.querySelector('.settings-btn').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    host.querySelectorAll('.settings-item')[0].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()

    expect(emitted.manageProjects).toBe(1)
  })
})
