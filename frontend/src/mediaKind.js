const AUDIO_EXTENSIONS = new Set(['.mp3'])
const PDF_EXTENSIONS = new Set(['.pdf'])
const MARKDOWN_EXTENSIONS = new Set(['.md'])

export function mediaKindFromUrl(url) {
  const path = url.split('?')[0].split('#')[0]
  const dot = path.lastIndexOf('.')
  const extension = dot === -1 ? '' : path.slice(dot).toLowerCase()
  if (AUDIO_EXTENSIONS.has(extension)) return 'audio'
  if (PDF_EXTENSIONS.has(extension)) return 'pdf'
  if (MARKDOWN_EXTENSIONS.has(extension)) return 'markdown'
  return 'image'
}
