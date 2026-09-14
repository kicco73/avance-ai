export const SOURCE_DRIVERS = [
  { value: 'avance', label: 'Avance Embedded' },
  { value: 'websearch', label: 'Avance Web Search' }
]

export const WEBSEARCH_DRIVER = 'websearch'

export function sourceDriverOf(source) {
  const url = source?.url ?? ''
  const separator = url.indexOf(':')
  return separator === -1 ? '' : url.slice(0, separator)
}
