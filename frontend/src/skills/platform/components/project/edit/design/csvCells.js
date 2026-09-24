import { EditModule } from 'tabulator-tables'
import { customDialog } from '../../../../../../dialogStore.js'
import CellMarkdownDialog from './CellMarkdownDialog.vue'

export const DISPLAY_LIMIT = 64

function text(cell) {
  return String(cell.getValue() ?? '')
}

async function editInDialog(cell, initialValue) {
  const value = await customDialog({
    component: CellMarkdownDialog,
    props: { title: cell.getField(), initialValue },
    wide: true
  })
  if (value === null) return
  cell.setValue(value)
}

function dialogEditor(cell) {
  editInDialog(cell, text(cell))
  return false
}

function textareaWithSwitch(cell, onRendered, success, cancel, params) {
  const textarea = EditModule.editors.textarea.call(this, cell, onRendered, success, cancel, params)
  textarea.style.paddingRight = '1.6rem'
  const toDialog = document.createElement('button')
  toDialog.type = 'button'
  toDialog.className = 'csv-cell-editor-switch'
  toDialog.title = 'Open in text editor'
  toDialog.textContent = '⤢'
  toDialog.addEventListener('mousedown', (event) => event.preventDefault())
  toDialog.addEventListener('click', () => {
    cancel()
    editInDialog(cell, textarea.value)
  })
  const host = document.createElement('div')
  host.className = 'csv-cell-editor'
  host.append(textarea, toDialog)
  return host
}

const numberCell = {
  accepts: (value) => value.trim() !== '' && Number.isFinite(Number(value)),
  display: (value) => value,
  editor: 'input'
}

const longTextCell = {
  accepts: (value) => value.length > DISPLAY_LIMIT,
  display: (value) => `${value.slice(0, DISPLAY_LIMIT)}…`,
  editor: dialogEditor
}

const shortTextCell = {
  accepts: () => true,
  display: (value) => value,
  editor: textareaWithSwitch
}

const CELL_KINDS = [numberCell, longTextCell, shortTextCell]

function kindOf(value) {
  return CELL_KINDS.find((kind) => kind.accepts(value))
}

function escapeHtml(raw) {
  return raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

export function csvColumn(field, { onDelete, onRename } = {}) {
  return {
    title: field,
    field,
    formatter: (cell) => document.createTextNode(kindOf(text(cell)).display(text(cell)) || ' '),
    editor: 'adaptable',
    editorParams: { editorLookup: (cell) => kindOf(text(cell)).editor },
    titleFormatter: () => (
      '<button type="button" class="csv-column-delete-btn" title="Delete this column">×</button>'
      + `<span class="csv-column-title" title="Double click to rename">${escapeHtml(field)}</span>`
    ),
    headerClick: (e, column) => {
      if (!e.target.closest('.csv-column-delete-btn')) return
      onDelete?.(column)
    },
    headerDblClick: (e, column) => {
      if (e.target.closest('.csv-column-delete-btn')) return
      onRename?.(column)
    }
  }
}

export const ROW_CONTROL_FIELD = '__row_control'

export function rowControlColumn(onDelete) {
  return {
    title: '',
    field: ROW_CONTROL_FIELD,
    width: 32,
    minWidth: 32,
    hozAlign: 'left',
    headerSort: false,
    resizable: false,
    formatter: () => '<button type="button" class="csv-row-delete-btn" title="Delete this row">×</button>',
    cellClick: (e, cell) => {
      if (!e.target.closest('.csv-row-delete-btn')) return
      onDelete?.(cell.getRow())
    }
  }
}
