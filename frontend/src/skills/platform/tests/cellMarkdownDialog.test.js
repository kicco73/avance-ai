import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'
import CellMarkdownDialog from '../components/project/edit/design/CellMarkdownDialog.vue'

describe('CellMarkdownDialog.vue', () => {
  let container
  let closeDialog

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    closeDialog = vi.fn()
  })

  afterEach(() => {
    container.remove()
  })

  async function mountDialog(initialValue) {
    const app = createApp(CellMarkdownDialog, { title: 'description', initialValue })
    app.provide('closeDialog', closeDialog)
    app.mount(container)
    await vi.waitFor(() => expect(container.querySelector('.milkdown .ProseMirror')).not.toBeNull())
    return app
  }

  it('renders the cell text as rich text under the column name', async () => {
    await mountDialog('A **long** description\n\nwith two paragraphs')

    expect(container.querySelector('h2').textContent).toBe('description')
    expect(container.querySelector('.milkdown strong')?.textContent).toBe('long')
    expect(container.querySelectorAll('.milkdown p').length).toBe(2)
  })

  it('OK returns the markdown, Cancel returns null', async () => {
    await mountDialog('A **long** description')

    container.querySelector('.cell-md-ok-btn').click()
    expect(closeDialog).toHaveBeenLastCalledWith('A **long** description')

    container.querySelector('.cell-md-cancel-btn').click()
    expect(closeDialog).toHaveBeenLastCalledWith(null)
  })
})
