import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive } from 'vue'

const { getAiCosts } = vi.hoisted(() => ({ getAiCosts: vi.fn() }))

vi.mock('../src/api.js', () => ({ getAiCosts }))
vi.mock('../src/components/services/TrendLineChart.vue', () => ({
  default: {
    props: ['history', 'providerLabels', 'title'],
    setup: (props) => () => h('pre', { class: 'chart-stub' }, JSON.stringify({ history: props.history, labels: props.providerLabels }))
  }
}))

const AiCostsPanel = (await import('../src/components/services/AiCostsPanel.vue')).default

const PROVIDERS = [{ driver: 'openai', model: 'mistral-small-latest', 'ui-label': 'Mistral' }]
const HISTORY = [
  { timestamp: '2026-09-24T00:00:00Z', values: { 'openai/mistral-small-latest': 0.12 } },
  { timestamp: '2026-09-25T00:00:00Z', values: { 'openai/mistral-small-latest': 0.3 } }
]

async function mount(props) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  createApp({ setup: () => () => h(AiCostsPanel, props) }).mount(host)
  await nextTick()
  await nextTick()
  return host
}

describe('AiCostsPanel', () => {
  afterEach(() => getAiCosts.mockReset())

  it('loads costs only once its tab is shown, and draws one line per provider under its configured name', async () => {
    getAiCosts.mockResolvedValue({ currency: 'EUR', history: HISTORY, unpriced_providers: [] })
    const props = reactive({ active: false, providers: PROVIDERS })

    const host = await mount(props)
    expect(getAiCosts).not.toHaveBeenCalled()

    props.active = true
    await nextTick()
    await nextTick()

    expect(getAiCosts).toHaveBeenCalledTimes(1)
    const chart = JSON.parse(host.querySelector('.chart-stub').textContent)
    expect(chart.history).toEqual(HISTORY)
    expect(chart.labels).toEqual({ 'openai/mistral-small-latest': 'Mistral' })
  })

  it('names the providers it could not price', async () => {
    getAiCosts.mockResolvedValue({ currency: 'EUR', history: HISTORY, unpriced_providers: ['gone/old-model'] })

    const host = await mount({ active: true, providers: PROVIDERS })

    expect(host.textContent).toContain('gone/old-model')
  })
})
