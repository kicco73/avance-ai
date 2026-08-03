// Inspector.vue's own state-scoping wiring for the Signals tab's "show
// only relevant signals" filter: a graph click (jump-to-definition's own
// `stateKey`, which means "the state itself" for a tapped node, or "the
// state a tapped action's own edge originates *from*" for a tapped
// action — see InspectorGraphTab.vue's edgeToCyData/matchStateKey)
// overrides the default (highlightedStateKey, the live/current state)
// until the live context itself moves on. Stubs the two real child tabs
// (cytoscape rendering and the actual signals list are out of scope
// here — see InspectorGraphTab.vue/InspectorSignalsTab.vue's own
// concerns) down to just enough to observe what Inspector.vue itself
// computes and passes through.
import { describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'

vi.mock('../src/components/inspector/InspectorGraphTab.vue', () => ({
  default: defineComponent({
    props: ['highlightedStateKey'],
    emits: ['jump-to-definition'],
    template: `
      <button class="tap-state" @click="$emit('jump-to-definition', { kind: 'state', stateKey: 'b' })">tap state b</button>
      <button class="tap-action" @click="$emit('jump-to-definition', { kind: 'action', stateKey: 'a', actionName: 'advance' })">tap action from a</button>
    `
  })
}))

vi.mock('../src/components/inspector/InspectorSignalsTab.vue', () => ({
  default: defineComponent({
    props: ['stateKey'],
    template: `<div class="signals-state-key">{{ stateKey }}</div>`
  })
}))

vi.mock('../src/components/inspector/InspectorMetricsTab.vue', () => ({ default: defineComponent({ template: '<div/>' }) }))
vi.mock('../src/components/inspector/InspectorEnvTab.vue', () => ({ default: defineComponent({ template: '<div/>' }) }))
vi.mock('../src/components/inspector/InspectorPerformanceTab.vue', () => ({ default: defineComponent({ template: '<div/>' }) }))

import Inspector from '../src/components/inspector/Inspector.vue'

async function mountInspector(props) {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const wrapper = defineComponent({
    props: Object.keys(props),
    render() {
      return h(Inspector, this.$props)
    }
  })
  const app = createApp(wrapper, props)
  app.mount(el)
  await nextTick()
  return { el, app }
}

function stateKeyText(el) {
  return el.querySelector('.signals-state-key').textContent
}

describe('Inspector.vue — Signals tab relevance scoping', () => {
  it('defaults the Signals tab state-key to highlightedStateKey', async () => {
    const { el, app } = await mountInspector({ projectName: 'proj', highlightedStateKey: 'a' })

    expect(stateKeyText(el)).toBe('a')

    app.unmount()
  })

  it('a tapped state node overrides the default with that state', async () => {
    const { el, app } = await mountInspector({ projectName: 'proj', highlightedStateKey: 'a' })

    el.querySelector('.tap-state').click()
    await nextTick()

    expect(stateKeyText(el)).toBe('b')

    app.unmount()
  })

  it("a tapped action edge uses the state it fires *from*, not its target", async () => {
    const { el, app } = await mountInspector({ projectName: 'proj', highlightedStateKey: 'b' })

    el.querySelector('.tap-action').click()
    await nextTick()

    expect(stateKeyText(el)).toBe('a')

    app.unmount()
  })

  it('the live context moving on (highlightedStateKey changes) drops the graph-click override', async () => {
    // A reactive wrapper around Inspector, so highlightedStateKey can
    // actually change on the *same* running instance — the exact thing
    // Inspector.vue's own watch(() => props.highlightedStateKey, ...)
    // needs to react to.
    const el = document.createElement('div')
    document.body.appendChild(el)
    const liveState = ref('a')
    const wrapper = defineComponent({
      render() {
        return h(Inspector, { projectName: 'proj', highlightedStateKey: liveState.value })
      }
    })
    const app = createApp(wrapper)
    app.mount(el)
    await nextTick()

    el.querySelector('.tap-state').click()
    await nextTick()
    expect(stateKeyText(el)).toBe('b') // the graph-click override is in effect

    liveState.value = 'c' // the conversation itself advanced
    await nextTick()
    expect(stateKeyText(el)).toBe('c') // override dropped, following the live state again

    app.unmount()
  })
})
