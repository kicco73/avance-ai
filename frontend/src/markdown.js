import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({
  breaks: true,
  linkify: true,
  typographer: true,
  html: false
})

// Links open outside the app rather than navigating the current page —
// in a home-screen standalone webapp there's no address bar/back button,
// so an in-place navigation stranded the user with no way back.
const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  token.attrSet('target', '_blank')
  token.attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

// Wraps every rendered table in a horizontally scrollable container so
// wide tables scroll instead of squeezing columns into word-splitting.
md.renderer.rules.table_open = () => '<div class="md-table-wrap"><table>'
md.renderer.rules.table_close = () => '</table></div>'

export function renderMarkdown(text) {
  if (!text) return ''
  return DOMPurify.sanitize(md.render(text), { ADD_ATTR: ['target'] })
}
