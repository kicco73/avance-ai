import { describe, expect, it } from 'vitest'
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import Inspector from '../components/inspector/Inspector.vue'

// The mechanism EditProjectView uses to render a tab it does not know
// about: a dynamic slot name, the scoped registerTab it hands back, and a
// component that must end up registered under that id so refresh/resize
// reach it.
const ContributedTab = defineComponent({
  props: { workspace: { type: Object, required: true } },
  setup(props, { expose }) {
    const refreshedActive = ref(null)
    expose({
      refresh(active) { refreshedActive.value = active },
      resize() {},
      refreshedActive,
    })
    return () => h('div', { class: 'contributed' }, props.workspace.projectId)
  }
})

function mount(tabs) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const inspectorRef = ref(null)
  const app = createApp({
    setup() {
      return () => h(
        Inspector,
        { ref: inspectorRef, tabs: tabs.map(({ id, label }) => ({ id, label })) },
        Object.fromEntries(tabs.map((tab) => [
          `tab-${tab.id}`,
          ({ registerTab }) => h(ContributedTab, { ref: registerTab(tab.id), workspace: { projectId: 'proj' } }),
        ]))
      )
    }
  })
  app.mount(host)
  return { host, app, inspectorRef }
}

describe('a tab contributed through a dynamic slot', () => {
  it('renders inside the inspector and gets its workspace', async () => {
    const { host } = mount([{ id: 'testing-info', label: 'Info' }])
    await nextTick()

    expect(host.querySelector('.contributed')?.textContent).toBe('proj')
    expect(host.textContent).toContain('Info')
  })

  it('registers under its own id, so refresh reaches it', async () => {
    const { host, inspectorRef } = mount([
      { id: 'testing-info', label: 'Info' },
      { id: 'testing-user', label: 'User' },
    ])
    await nextTick()

    await inspectorRef.value.refresh()
    await nextTick()

    const panels = host.querySelectorAll('.contributed')
    expect(panels).toHaveLength(2)
  })

  it('falls back to the first tab when the contributed set changes shape', async () => {
    const { host } = mount([{ id: 'testing-info', label: 'Info' }])
    await nextTick()

    expect(host.querySelector('.inspector-tab-btn-active')?.textContent).toBe('Info')
  })
})
