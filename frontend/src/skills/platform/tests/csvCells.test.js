import { beforeEach, describe, expect, it, vi } from 'vitest'
import { customDialog } from '../../../dialogStore.js'
import { DISPLAY_LIMIT, csvColumn } from '../components/project/edit/design/csvCells.js'

vi.mock('../../../dialogStore.js', () => ({ customDialog: vi.fn() }))

const EXACT = 'x'.repeat(DISPLAY_LIMIT)
const LONG = `${EXACT} and more`

function fakeCell(value) {
  return {
    getValue: () => value,
    getField: () => 'notes',
    setValue: vi.fn(),
    getType: () => 'cell',
    getRow: () => ({ normalizeHeight() {} })
  }
}

const column = csvColumn('notes')
const displayOf = (value) => column.formatter(fakeCell(value)).textContent
const editorOf = (cell) => column.editorParams.editorLookup(cell)
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

function openEditor(cell) {
  const success = vi.fn()
  const cancel = vi.fn()
  const element = editorOf(cell).call({}, cell, (fn) => fn(), success, cancel, {})
  return { element, success, cancel }
}

describe('csvColumn', () => {
  beforeEach(() => {
    customDialog.mockReset()
  })

  it('shows at most DISPLAY_LIMIT characters, then an ellipsis', () => {
    expect(displayOf(LONG)).toBe(`${EXACT}…`)
    expect(displayOf(EXACT)).toBe(EXACT)
    expect(displayOf('42')).toBe('42')
    expect(displayOf(undefined)).toBe(' ')
  })

  it('numbers edit inline with the plain input editor', () => {
    expect(editorOf(fakeCell('42'))).toBe('input')
    expect(editorOf(fakeCell('-3.5'))).toBe('input')
    expect(editorOf(fakeCell('1e3'))).toBe('input')
  })

  it('long text opens the markdown dialog and writes the result into the cell', async () => {
    customDialog.mockResolvedValueOnce('rewritten')
    const cell = fakeCell(LONG)

    const { element } = openEditor(cell)
    await flush()

    expect(element).toBe(false)
    expect(customDialog).toHaveBeenCalledWith(expect.objectContaining({ props: { title: 'notes', initialValue: LONG } }))
    expect(cell.setValue).toHaveBeenCalledWith('rewritten')
  })

  it('cancelling the dialog leaves the cell untouched', async () => {
    customDialog.mockResolvedValueOnce(null)
    const cell = fakeCell(LONG)

    openEditor(cell)
    await flush()

    expect(cell.setValue).not.toHaveBeenCalled()
  })

  it('short text edits in a textarea that submits on blur', () => {
    const cell = fakeCell('short')
    const { element, success } = openEditor(cell)
    const textarea = element.querySelector('textarea')

    expect(textarea.value).toBe('short')
    textarea.value = 'shorter'
    textarea.dispatchEvent(new Event('blur'))

    expect(success).toHaveBeenCalledWith('shorter')
  })

  it('the switch button carries the typed text into the markdown dialog', async () => {
    customDialog.mockResolvedValueOnce('final')
    const cell = fakeCell('short')
    const { element, success, cancel } = openEditor(cell)
    const textarea = element.querySelector('textarea')

    textarea.value = 'draft'
    element.querySelector('.csv-cell-editor-switch').click()
    await flush()

    expect(cancel).toHaveBeenCalled()
    expect(success).not.toHaveBeenCalled()
    expect(customDialog).toHaveBeenCalledWith(expect.objectContaining({ props: { title: 'notes', initialValue: 'draft' } }))
    expect(cell.setValue).toHaveBeenCalledWith('final')
  })

  it('a double click on the header renames the column, never on its delete button', () => {
    const onRename = vi.fn()
    const onDelete = vi.fn()
    const named = csvColumn('notes', { onRename, onDelete })
    const header = document.createElement('div')
    header.innerHTML = named.titleFormatter()
    const fakeColumn = {}

    named.headerDblClick({ target: header.querySelector('.csv-column-title') }, fakeColumn)
    named.headerDblClick({ target: header.querySelector('.csv-column-delete-btn') }, fakeColumn)

    expect(onRename).toHaveBeenCalledTimes(1)
    expect(onRename).toHaveBeenCalledWith(fakeColumn)
    expect(onDelete).not.toHaveBeenCalled()
  })
})
