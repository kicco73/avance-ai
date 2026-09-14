import Papa from 'papaparse'

export function parseCsvRows(text) {
  const result = Papa.parse(text ?? '', { header: true, skipEmptyLines: true })
  return { fields: result.meta.fields ?? [], data: result.data }
}
