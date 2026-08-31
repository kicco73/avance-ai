import { describe, expect, it } from 'vitest'
import { activeDialog, chooseDialog, confirmDialog, customDialog, infoDialog, promptDialog, resolveActiveDialog } from '../src/dialogStore.js'

describe('dialogStore', () => {
  it('confirmDialog resolves true/false off resolveActiveDialog', async () => {
    const pending = confirmDialog({ title: 'Delete', body: 'Sure?' })
    expect(activeDialog.value.kind).toBe('confirm')
    expect(activeDialog.value.title).toBe('Delete')

    resolveActiveDialog(true)
    await expect(pending).resolves.toBe(true)
    expect(activeDialog.value).toBe(null)
  })

  it('promptDialog resolves the given string, or null when cancelled', async () => {
    const pending = promptDialog({ title: 'Rename', body: 'New name:' })
    resolveActiveDialog('notes.txt')
    await expect(pending).resolves.toBe('notes.txt')

    const cancelled = promptDialog({ title: 'Rename', body: 'New name:' })
    resolveActiveDialog(null)
    await expect(cancelled).resolves.toBe(null)
  })

  it('chooseDialog resolves the chosen option id, or null when cancelled', async () => {
    const pending = chooseDialog({
      title: 'Unsaved changes',
      body: 'Save first?',
      options: [{ id: 'save', label: 'Save' }, { id: 'discard', label: 'Discard' }]
    })
    resolveActiveDialog('discard')
    await expect(pending).resolves.toBe('discard')
  })

  it('infoDialog resolves once closed', async () => {
    const pending = infoDialog({ title: 'About', body: 'Version 1.0' })
    resolveActiveDialog(true)
    await expect(pending).resolves.toBe(true)
  })

  it('customDialog carries the given component/props through as kind "custom", resolving once closed', async () => {
    const FakeComponent = { name: 'FakeComponent' }
    const pending = customDialog({ component: FakeComponent, props: { projectName: 'proj' } })

    expect(activeDialog.value.kind).toBe('custom')
    expect(activeDialog.value.component).toBe(FakeComponent)
    expect(activeDialog.value.props).toEqual({ projectName: 'proj' })

    resolveActiveDialog(null)
    await expect(pending).resolves.toBe(null)
  })

  it('customDialog defaults props to an empty object when omitted', () => {
    customDialog({ component: {} })
    expect(activeDialog.value.props).toEqual({})
    resolveActiveDialog(null)
  })

  it('only ever shows one dialog at a time — a second request waits for the first to resolve', async () => {
    const first = confirmDialog({ title: 'First', body: '...' })
    const second = confirmDialog({ title: 'Second', body: '...' })

    // The second request is queued, not shown or replacing the first.
    expect(activeDialog.value.title).toBe('First')

    resolveActiveDialog(true)
    await first

    // Resolving the first immediately promotes the second.
    expect(activeDialog.value.title).toBe('Second')

    resolveActiveDialog(false)
    await expect(second).resolves.toBe(false)
    expect(activeDialog.value).toBe(null)
  })

  it('resolveActiveDialog is a no-op when nothing is active', () => {
    expect(activeDialog.value).toBe(null)
    expect(() => resolveActiveDialog(true)).not.toThrow()
  })
})
