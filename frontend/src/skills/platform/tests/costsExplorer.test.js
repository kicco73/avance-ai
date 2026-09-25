import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick } from 'vue'

const api = vi.hoisted(() => ({
  getCostApps: vi.fn(), getCostUsers: vi.fn(), getCostSessions: vi.fn(), getCostSeries: vi.fn(), getCostTurns: vi.fn()
}))

vi.mock('../api.js', () => api)
vi.mock('../components/services/CostDistributionChart.vue', () => ({
  default: { props: ['bins', 'currency'], setup: (props) => () => h('pre', { class: 'distribution-stub' }, JSON.stringify(props.bins)) }
}))
vi.mock('../../../components/services/TrendLineChart.vue', () => ({
  default: { props: ['history', 'title'], setup: (props) => () => h('pre', { class: 'chart-stub' }, JSON.stringify(props.history)) }
}))

const CostsExplorer = (await import('../components/services/CostsExplorer.vue')).default

const HISTORY = [
  { timestamp: '2026-09-24T00:00:00Z', values: { 'openai/mistral': 0.5 } },
  { timestamp: '2026-09-25T00:00:00Z', values: { 'openai/mistral': 1.5 } }
]
const SUMMARY = { last_24_hours: 1.5, last_7_days: 2, mean_per_day: 1, mean_per_week: 7 }

async function settle() {
  for (let i = 0; i < 4; i++) await nextTick()
}

async function mount() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  createApp({ setup: () => () => h(CostsExplorer) }).mount(host)
  await settle()
  return host
}

function rowLabels(host) {
  return [...host.querySelectorAll('.split-explorer-item')].map((row) => row.textContent.replace(/[▸▾]/g, '').trim())
}

function click(host, label) {
  [...host.querySelectorAll('.split-explorer-item')].find((row) => row.textContent.includes(label)).click()
}

describe('CostsExplorer', () => {
  afterEach(() => Object.values(api).forEach((fn) => fn.mockReset()))

  it('shows the per-turn distribution on the app and Users pages only', async () => {
    const bins = [{ from: 0.001, to: 0.002, count: 3 }]
    api.getCostApps.mockResolvedValue({ apps: [{ id: 'app', label: 'App' }] })
    api.getCostUsers.mockResolvedValue({ users: [] })
    api.getCostSeries.mockResolvedValue({ currency: 'EUR', history: [], summary: SUMMARY, unpriced_providers: [] })
    api.getCostTurns.mockResolvedValue({ currency: 'EUR', turns: 3, mean: 0.0015, median: 0.0015, extra_per_turn: 0.0004, bins })

    const host = await mount()
    click(host, 'App')
    await settle()
    expect(api.getCostTurns).toHaveBeenLastCalledWith({ projectId: 'app', scope: 'app' })
    expect(JSON.parse(host.querySelector('.distribution-stub').textContent)).toEqual(bins)
    expect(host.textContent).toContain('Extras per turn')

    click(host, 'Users')
    await settle()
    expect(api.getCostTurns).toHaveBeenLastCalledWith({ projectId: 'app', scope: 'users' })

    click(host, 'Test')
    await settle()
    expect(api.getCostTurns).toHaveBeenCalledTimes(2)
    expect(host.querySelector('.distribution-stub')).toBeNull()
  })

  it('opens an app into Test, Preview, Run and Users, and each node shows its own costs', async () => {
    api.getCostApps.mockResolvedValue({ apps: [{ id: 'fake_manuel_2', label: 'Fake Manuel+' }] })
    api.getCostUsers.mockResolvedValue({ users: [{ id: 'alice', label: 'Alice' }] })
    api.getCostSessions.mockResolvedValue({ sessions: [{ id: 42, label: 'First visit' }] })
    api.getCostSeries.mockResolvedValue({ currency: 'EUR', history: HISTORY, summary: SUMMARY, unpriced_providers: [] })

    const host = await mount()
    expect(rowLabels(host)).toEqual(['Fake Manuel+'])

    click(host, 'Fake Manuel+')
    await settle()
    expect(api.getCostSeries).toHaveBeenLastCalledWith({ projectId: 'fake_manuel_2', scope: 'app' })
    expect(rowLabels(host)).toEqual(['Fake Manuel+', 'Test', 'Preview', 'Run', 'Users'])
    expect(host.querySelector('.costs-summary').textContent).toContain('Mean per week')
    expect(JSON.parse(host.querySelector('.chart-stub').textContent)).toEqual(HISTORY)

    click(host, 'Test')
    await settle()
    expect(api.getCostSeries).toHaveBeenLastCalledWith({ projectId: 'fake_manuel_2', scope: 'test' })

    click(host, 'Users')
    await settle()
    expect(api.getCostSeries).toHaveBeenLastCalledWith({ projectId: 'fake_manuel_2', scope: 'users' })
    expect(rowLabels(host)).toEqual(['Fake Manuel+', 'Test', 'Preview', 'Run', 'Users', 'Alice'])

    click(host, 'Alice')
    await settle()
    expect(api.getCostSeries).toHaveBeenLastCalledWith({ projectId: 'fake_manuel_2', scope: 'users', username: 'alice' })
    expect(rowLabels(host)).toContain('First visit')

    click(host, 'First visit')
    await settle()
    expect(api.getCostSeries).toHaveBeenLastCalledWith({
      projectId: 'fake_manuel_2', scope: 'users', username: 'alice', sessionId: 42
    })
  })

  it('collapses an open node when it is clicked again', async () => {
    api.getCostApps.mockResolvedValue({ apps: [{ id: 'app', label: 'App' }] })
    api.getCostUsers.mockResolvedValue({ users: [{ id: 'alice', label: 'Alice' }] })
    api.getCostSeries.mockResolvedValue({ currency: 'EUR', history: [], summary: SUMMARY, unpriced_providers: [] })

    const host = await mount()
    click(host, 'App')
    await settle()
    click(host, 'App')
    await settle()

    expect(rowLabels(host)).toEqual(['App'])
  })
})
