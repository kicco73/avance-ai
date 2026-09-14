import { describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref } from 'vue'

const envKeys = [
  { env_key: { name: 'alpha', ai_definition: 'what alpha means' } },
  { env_key: { name: 'beta', ai_definition: 'what beta means' } },
  { env_key: { name: 'undefined_one', ai_definition: null } },
]

vi.mock('../api.js', () => ({
  getProjectEnvKeys: async () => ({ env_keys: envKeys }),
}))

const InspectorStateIOTab = (await import('../components/inspector/InspectorStateIOTab.vue')).default

async function mount(stateKey, stateData, saveField = vi.fn(), listeners = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const selection = ref({ stateKey, stateData })
  const app = createApp({
    setup: () => () => h(InspectorStateIOTab, {
      projectId: 'p',
      stateKey: selection.value.stateKey,
      stateData: selection.value.stateData,
      saveField,
      ...listeners,
    })
  })
  app.mount(host)
  await nextTick()
  await nextTick()
  await nextTick()
  return { host, selection }
}

function boxes(host, field) {
  const block = host.querySelectorAll('.inspector-io-block')[field === 'input' ? 0 : 1]
  return [...block.querySelectorAll('label')].map((label) => ({
    name: label.querySelector('.inspector-io-name').textContent,
    checked: label.querySelector('input').checked,
    disabled: label.querySelector('input').disabled,
  }))
}

describe('the state I/O tab', () => {
  it('shows nothing to tick until a state is selected', async () => {
    const { host } = await mount(null, null)

    expect(host.querySelectorAll('input[type=checkbox]').length).toBe(0)
  })

  it('follows the selection: a new state key and data re-tick every box', async () => {
    const { host, selection } = await mount('s1', { input: ['alpha'], output: [] })

    expect(boxes(host, 'input').map((b) => b.checked)).toEqual([true, false, false])
    expect(boxes(host, 'output').map((b) => b.checked)).toEqual([false, false, false])

    selection.value = { stateKey: 's2', stateData: { input: ['beta'], output: ['alpha'] } }
    await nextTick()

    expect(boxes(host, 'input').map((b) => b.checked)).toEqual([false, true, false])
    expect(boxes(host, 'output').map((b) => b.checked)).toEqual([true, false, false])
  })

  it('keeps the tick while the write is in flight, and drops it if the write never lands', async () => {
    let land
    const setField = vi.fn(() => new Promise((resolve) => { land = resolve }))
    const { host } = await mount('s1', { input: [], output: [] }, setField)
    const box = host.querySelectorAll('input[type=checkbox]')[0]

    box.checked = true
    box.dispatchEvent(new Event('change'))
    await nextTick()

    expect(setField).toHaveBeenCalledWith('input', ['alpha'])
    expect(box.checked).toBe(true)

    land(false)
    await nextTick()
    await nextTick()

    expect(box.checked).toBe(false)
  })

  it('keeps the tick when the write lands and the state comes back listing it', async () => {
    let land
    const setField = vi.fn(() => new Promise((resolve) => { land = resolve }))
    const { host, selection } = await mount('s1', { input: [], output: [] }, setField)
    const box = host.querySelectorAll('input[type=checkbox]')[0]

    box.checked = true
    box.dispatchEvent(new Event('change'))
    await nextTick()

    selection.value = { stateKey: 's1', stateData: { input: ['alpha'], output: [] } }
    land(true)
    await nextTick()
    await nextTick()

    expect(box.checked).toBe(true)
  })

  it('hides the env declarations while a state is selected', async () => {
    const { host } = await mount('s1', { input: [], output: [] })

    expect(host.querySelector('.inspector-env-keys')).toBe(null)
  })

  it('declares env keys with no state selected', async () => {
    const onAddEnvKey = vi.fn()
    const { host } = await mount(null, null, vi.fn(), { onAddEnvKey })

    expect([...host.querySelectorAll('.inspector-signal-name')].map((n) => n.textContent))
      .toContain('alpha')

    host.querySelector('.inspector-signals-add-btn').click()
    expect(onAddEnvKey).toHaveBeenCalled()
  })

  it('renames an env key through the tab that owns the I/O', async () => {
    const onSetEnvKeyField = vi.fn()
    const { host } = await mount(null, null, vi.fn(), { onSetEnvKeyField })

    const card = [...host.querySelectorAll('.inspector-signal-block')]
      .find((b) => b.textContent.includes('alpha'))
    vi.useFakeTimers()
    card.click()
    await vi.advanceTimersByTimeAsync(1000)
    vi.useRealTimers()

    const nameInput = host.querySelector('.inspector-signal-label-input')
    nameInput.value = 'renamed'
    nameInput.dispatchEvent(new Event('input'))
    await nextTick()
    nameInput.dispatchEvent(new Event('blur'))

    expect(onSetEnvKeyField).toHaveBeenCalledWith('alpha', 'name', 'renamed')
  })

  it('refuses a variable the model could not be told about', async () => {
    const { host } = await mount('s1', { input: [], output: [] })

    const undefinedOne = boxes(host, 'input')[2]
    expect(undefinedOne.name).toBe('undefined_one')
    expect(undefinedOne.disabled).toBe(true)
    expect(host.textContent).toContain('needs an AI definition')
  })
})
