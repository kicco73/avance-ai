export const SKIN_SCOPE_SELECTOR = '.chat-window-shell'

const DOCUMENT_LEVEL_AT_RULE = /^@(?:-webkit-)?(?:keyframes|font-face|property|import|charset|namespace)\b/i
const NESTING_AT_RULE = /^@(?:media|supports|container|layer|scope)\b/i

function splitTopLevel(text, separators) {
  const parts = []
  let depth = 0
  let start = 0
  let quote = null
  for (let i = 0; i < text.length; i++) {
    const char = text[i]
    if (quote) {
      if (char === '\\') i++
      else if (char === quote) quote = null
      continue
    }
    if (char === '"' || char === "'") {
      quote = char
      continue
    }
    if (char === '/' && text[i + 1] === '*') {
      const end = text.indexOf('*/', i + 2)
      i = end === -1 ? text.length : end + 1
      continue
    }
    if (char === '(' || char === '[' || char === '{') depth++
    else if (char === ')' || char === ']' || char === '}') {
      depth--
      if (depth === 0 && separators.includes('}')) {
        parts.push(text.slice(start, i + 1))
        start = i + 1
      }
    } else if (depth === 0 && separators.includes(char)) {
      parts.push(text.slice(start, i + 1))
      start = i + 1
    }
  }
  parts.push(text.slice(start))
  return parts.filter((part) => part.trim())
}

function rules(cssText) {
  return splitTopLevel(cssText, ['}', ';'])
}

function selectorList(cssText) {
  return splitTopLevel(cssText, [','])
    .map((part) => part.replace(/,\s*$/, '').trim())
    .filter(Boolean)
}

const ROOT_MATCHABLE = /^[.#[:]/

function scopedSelector(selector) {
  return selectorList(selector)
    .flatMap((one) => (ROOT_MATCHABLE.test(one) ? [one, `:scope${one}`] : [one]))
    .join(', ')
}

function scopeRule(rule) {
  const braceAt = rule.indexOf('{')
  if (braceAt === -1) return rule
  const head = rule.slice(0, braceAt).trim()
  const body = rule.slice(braceAt + 1, rule.lastIndexOf('}'))
  if (NESTING_AT_RULE.test(head)) return `${head} {\n${scopeRules(body)}\n}`
  if (head.startsWith('@')) return rule
  return `${scopedSelector(head)} {${body}}`
}

function scopeRules(cssText) {
  return rules(cssText).map((rule) => scopeRule(rule.trim())).join('\n')
}

export function scopeSkinToChat(cssText) {
  const documentLevel = []
  const scoped = []
  for (const rule of rules(cssText)) {
    const target = DOCUMENT_LEVEL_AT_RULE.test(rule.trim()) ? documentLevel : scoped
    target.push(rule.trim())
  }
  const scopedBlock = scoped.length
    ? [`@scope (${SKIN_SCOPE_SELECTOR}) {`, scopeRules(scoped.join('\n')), '}']
    : []
  return [...documentLevel, ...scopedBlock].join('\n')
}
