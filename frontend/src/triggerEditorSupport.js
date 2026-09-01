// Pure, framework-agnostic logic behind TriggerEditor.vue's autocomplete
// and syntax-coloring. `completeIdentifiers` only depends on
// CompletionContext's shape (matchBefore), never a live EditorView/DOM.
import { snippetCompletion } from '@codemirror/autocomplete'

// Known parameter names for a proxy-style (call) identifier, purely to
// build a fill-in-the-blanks snippet completion — the registry itself
// only carries a free-text description, never a structured signature.
const CALL_PARAMS = {
  'source.attachment': ['name'],
  'actuator.send_mail': ['to', 'body_md']
}

// Fixed per-namespace colors — frontend-only, the identifier registry
// never transports styling. "session.metric" gets its own distinct
// color, not session's — it's its own registry key.
export const NAMESPACE_COLORS = {
  signal: '#1565c0',
  env: '#00838f',
  system: '#8a6d3b',
  session: '#6a1b9a',
  'session.metric': '#ad1457',
  user: '#d84315',
  source: '#3949ab',
  actuator: '#c62828',
  metric: '#2e7d32',
  automaton: '#455a64'
}

// signal/env/user are plain variables (env/user resolve straight off an
// already-fetched dict); every other fixed namespace is call-style —
// system/session take no arguments, source's own methods (one per
// tracking/sources/ module, e.g. attachment(name)) take theirs inside
// the same parens completion inserts empty. This decides a completion's
// `type`/`apply` (append "()" or not), never a label.
export function isProxyNamespace(namespace) {
  return namespace !== 'signal' && namespace !== 'env' && namespace !== 'user' && namespace !== 'automaton' && !namespace.startsWith('automaton.')
}

// Matches a complete namespace reference (e.g. "signal.mood") anywhere
// in the text — group 1 is the namespace path, used to look up its color
// (NAMESPACE_COLORS). Always construct a fresh RegExp — /g carries state via lastIndex.
export const REFERENCE_PATTERN_SOURCE = '\\b(signal|env|system|session(?:\\.metric)?|user|source|actuator|metric|automaton)\\.[A-Za-z_]\\w*'

export function namespaceOf(referenceText) {
  const match = new RegExp(`^${REFERENCE_PATTERN_SOURCE}`).exec(referenceText)
  return match ? match[1] : null
}

// A short bracketed tag per completion `type` (variable/function/
// namespace) shown ahead of the identifier's name in its info panel (see
// completionInfo below) — distinct from CodeMirror's own per-row icon.
const COMPLETION_SYMBOL = {
  variable: '[var]',
  function: '[fn]',
  namespace: '[ns]'
}

// Completion.info — unlike `detail` (rendered inline, clipped by
// ellipsis), `info` renders in its own side panel, never truncated.
// Returns a plain DOM node, since CodeMirror renders it as-is.
export function completionInfo(name, description, type) {
  const root = document.createElement('div')
  root.className = 'cm-trigger-completion-info'

  const header = document.createElement('div')
  header.className = 'cm-trigger-completion-info-header'
  const symbol = document.createElement('span')
  symbol.className = 'cm-trigger-completion-info-symbol'
  symbol.textContent = COMPLETION_SYMBOL[type] ?? ''
  const label = document.createElement('strong')
  label.textContent = name
  header.append(symbol, document.createTextNode(': '), label)
  root.append(header)

  if (description) {
    const body = document.createElement('div')
    body.className = 'cm-trigger-completion-info-description'
    body.textContent = description
    root.append(body)
  }

  return root
}

// Every direct child sub-namespace of `namespace`, derived purely from
// which dotted registry keys exist, never a hardcoded list — e.g.
// "session" has "session.metric" in the registry, so "metric" is a child.
function directChildNamespaces(registry, namespace) {
  const prefix = `${namespace}.`
  const children = new Set()
  for (const key of Object.keys(registry)) {
    if (!key.startsWith(prefix)) continue
    children.add(key.slice(prefix.length).split('.')[0])
  }
  return [...children]
}

// The one completion source TriggerEditor's autocompletion() registers.
// Right after a namespace's dot (e.g. "signal.") offers every identifier
// under it plus child sub-namespaces; otherwise, every top-level namespace name.
export function completeIdentifiers(context, registry) {
  const dotted = context.matchBefore(/[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\.\w*$/)
  if (dotted) {
    const lastDot = dotted.text.lastIndexOf('.')
    const namespace = dotted.text.slice(0, lastDot)
    const from = dotted.from + lastDot + 1
    const identifiers = registry[namespace] ?? {}
    const options = Object.entries(identifiers).map(([name, description]) => {
      const isCall = isProxyNamespace(namespace)
      const type = isCall ? 'function' : 'variable'
      const info = () => completionInfo(name, description, type)
      const params = isCall ? CALL_PARAMS[`${namespace}.${name}`] : null
      if (params) {
        const template = `${name}(${params.map((param) => '${' + param + '}').join(', ')})`
        return snippetCompletion(template, { label: name, type, info })
      }
      return {
        label: name,
        // Not `detail` — see completionInfo's own docstring on why a
        // longer description belongs in `info` instead.
        info,
        type,
        apply: isCall ? `${name}()` : name
      }
    })
    for (const child of directChildNamespaces(registry, namespace)) {
      options.push({ label: child, type: 'namespace', apply: child })
    }
    if (!options.length) return null
    return { from, options }
  }

  // \w* (not \w+): must still match a zero-length position so an
  // explicit request (Ctrl+Space) with nothing typed yet still gets
  // every namespace suggested.
  const word = context.matchBefore(/\w*$/)
  if (!word || (word.from === word.to && !context.explicit)) return null
  // Returned unfiltered by `word.text` — CodeMirror does its own fuzzy
  // matching; pre-filtering with startsWith would be stricter and could
  // silently hide a namespace a fuzzy match would still offer.
  const namespaces = Object.keys(registry).filter((ns) => !ns.includes('.'))
  const options = namespaces.map((ns) => ({ label: ns, type: 'namespace', apply: ns }))
  if (!options.length) return null
  return { from: word.from, options }
}
