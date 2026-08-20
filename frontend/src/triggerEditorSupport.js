// Pure, framework-agnostic logic behind TriggerEditor.vue's own
// autocomplete/syntax-coloring — extracted so both have a real,
// repo-resident regression test instead of only ever being eyeballed
// through a live CodeMirror instance (same reasoning as benchmarkTimeline.
// js's own module docstring). `completeIdentifiers` only depends on
// @codemirror/autocomplete's own CompletionContext shape (matchBefore),
// never on a live EditorView/DOM — real CompletionContext instances are
// cheap to construct off a plain EditorState in a test, no jsdom needed.

// Fixed per-namespace colors — frontend-only, the identifier registry
// (see api.js's getIdentifiers) never transports styling. "session.metric"
// gets its own distinct color, not session's — it's its own registry key,
// meaningfully different from a plain session.* reference.
export const NAMESPACE_COLORS = {
  signal: '#1565c0',
  env: '#00838f',
  system: '#8a6d3b',
  session: '#6a1b9a',
  'session.metric': '#ad1457',
  metric: '#2e7d32'
}

// signal/env are plain variables; every other namespace is a zero-arg
// proxy, always called with () (see automaton.identifier_registry's own
// docstring: "signal ed env variabili, gli altri proxy") — this is what
// decides a completion's own `type`/`apply` (append "()" or not), never
// a label.
export function isProxyNamespace(namespace) {
  return namespace !== 'signal' && namespace !== 'env'
}

// Matches a complete namespace reference (e.g. "signal.mood",
// "session.metric.engagement") anywhere in the text — group 1 is the
// namespace path, used to look up its own fixed color (see
// NAMESPACE_COLORS). Doesn't match the trailing "()" a proxy reference
// is always called with — left in the editor's own default color, since
// that's already what visually marks a proxy namespace apart from a
// plain variable one, no extra coloring needed on top. Always construct
// a *fresh* RegExp from this (see its own /g flag — a shared instance
// would carry state across unrelated calls via lastIndex).
export const REFERENCE_PATTERN_SOURCE = '\\b(signal|env|system|session(?:\\.metric)?|metric)\\.[A-Za-z_]\\w*'

export function namespaceOf(referenceText) {
  const match = new RegExp(`^${REFERENCE_PATTERN_SOURCE}`).exec(referenceText)
  return match ? match[1] : null
}

// A short bracketed tag per completion `type` — the same three types
// completeIdentifiers itself ever hands out (variable/function/
// namespace, see isProxyNamespace) — shown ahead of the identifier's own
// name in its info panel (see completionInfo below). Not the same thing
// as CodeMirror's own per-row icon (driven by `type` directly, via its
// own .cm-completionIcon-* CSS) — this is this project's own, inside the
// info panel itself.
const COMPLETION_SYMBOL = {
  variable: '[var]',
  function: '[fn]',
  namespace: '[ns]'
}

// Completion.info (see @codemirror/autocomplete) — unlike `detail`
// (rendered inline in the completion list itself, one line, clipped with
// an ellipsis past the list's own width — exactly what was cutting a
// longer signal/metric ui-description off), `info` renders in its own
// side panel next to the list, sized to its actual content: never
// truncated, free to wrap across as many lines as the description
// itself needs. A plain DOM node (jsdom-constructible, no live
// EditorView needed — see this module's own docstring) rather than a
// markdown/HTML string: CodeMirror renders whatever Node info() returns
// as-is, and a real <strong> is simpler and safer here than hand-rolling
// markdown parsing just for one bold run.
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

// The one completion source TriggerEditor.vue's own autocompletion()
// registers. Two cases: right after a namespace's own dot (e.g.
// "signal." or "session.metric.") — every identifier `registry` lists
// under that exact namespace, each annotated with its own description;
// otherwise, a bare word being typed (e.g. "sig") — every top-level
// namespace name (a registry key with no "." of its own) starting with
// it. "session" additionally always offers "metric" itself, a nested
// sub-namespace rather than one of its own direct identifiers (see
// registry's own "session.metric" key), so it's still discoverable by
// typing "session." alone.
export function completeIdentifiers(context, registry) {
  const dotted = context.matchBefore(/[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\.\w*$/)
  if (dotted) {
    const lastDot = dotted.text.lastIndexOf('.')
    const namespace = dotted.text.slice(0, lastDot)
    const from = dotted.from + lastDot + 1
    const identifiers = registry[namespace] ?? {}
    const options = Object.entries(identifiers).map(([name, description]) => {
      const type = isProxyNamespace(namespace) ? 'function' : 'variable'
      return {
        label: name,
        // Not `detail` — see completionInfo's own docstring on why a
        // longer ui-description belongs in `info` instead, never
        // truncated inline in the list itself.
        info: () => completionInfo(name, description, type),
        type,
        apply: isProxyNamespace(namespace) ? `${name}()` : name
      }
    })
    if (namespace === 'session') {
      options.push({ label: 'metric', type: 'namespace', apply: 'metric' })
    }
    if (!options.length) return null
    return { from, options }
  }

  // \w* (not \w+): must still match a *zero-length* position so an
  // explicit request (Ctrl+Space) with nothing typed yet — e.g. a blank
  // trigger field — still gets every namespace suggested, rather than
  // matchBefore itself returning null before context.explicit is ever
  // even consulted.
  const word = context.matchBefore(/\w*$/)
  if (!word || (word.from === word.to && !context.explicit)) return null
  // Every namespace is returned unfiltered by `word.text` here — same
  // reasoning as the identifiers branch above: "these don't have to be
  // compared with the input by the source — the autocompletion system
  // will do its own matching" (see @codemirror/autocomplete's own
  // CompletionResult.options docs). Pre-filtering with startsWith here
  // would also be *stricter* than CodeMirror's own fuzzy matching,
  // silently hiding a namespace a fuzzy match would still have offered.
  const namespaces = Object.keys(registry).filter((ns) => !ns.includes('.'))
  const options = namespaces.map((ns) => ({ label: ns, type: 'namespace', apply: ns }))
  if (!options.length) return null
  return { from: word.from, options }
}
