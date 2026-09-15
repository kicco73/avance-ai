import { snippetCompletion } from '@codemirror/autocomplete'

const CALL_PARAMS = {
  'task.send_mail': ['to', 'body_md'],
  'task.websearch': ['query'],
  'chat.notify': ['title', 'body_md'],
  'chat.show': ['body_md'],
  'chat.switch_to_human': ['user_id']
}

const EVENT_EMPTY_HINT =
  "No other project declares the same project.family — set it in this project's and a sibling's index.yml to reference event.<id>."

export const NAMESPACE_COLORS = {
  signal: '#1565c0',
  env: '#00838f',
  session: '#6a1b9a',
  'session.metric': '#ad1457',
  user: '#d84315',
  source: '#3949ab',
  task: '#c62828',
  chat: '#f9a825',
  metric: '#2e7d32',
  event: '#455a64',
  choice: '#5d4037',
  datetime: '#00695c',
  'datetime.timezone': '#00897b'
}

export function excludingNamespaces(registry, excluded) {
  if (!excluded || !excluded.length) return registry
  const isExcluded = (ns) => excluded.some((x) => ns === x || ns.startsWith(x + '.'))
  return Object.fromEntries(Object.entries(registry).filter(([ns]) => !isExcluded(ns)))
}

export function isProxyNamespace(namespace) {
  return namespace !== 'signal' && namespace !== 'env' && namespace !== 'user' &&
    namespace !== 'event' && namespace !== 'datetime.timezone' && !namespace.startsWith('event.')
}

export const REFERENCE_PATTERN_SOURCE = '\\b(signal|env|session(?:\\.metric)?|user|source|task|chat|metric|event|choice|datetime(?:\\.timezone)?)\\.[A-Za-z_]\\w*'

export function namespaceOf(referenceText) {
  const match = new RegExp(`^${REFERENCE_PATTERN_SOURCE}`).exec(referenceText)
  return match ? match[1] : null
}

const COMPLETION_SYMBOL = {
  variable: '[var]',
  function: '[fn]',
  namespace: '[ns]'
}

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

function directChildNamespaces(registry, namespace) {
  const prefix = `${namespace}.`
  const children = new Set()
  for (const key of Object.keys(registry)) {
    if (!key.startsWith(prefix)) continue
    children.add(key.slice(prefix.length).split('.')[0])
  }
  return [...children]
}

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
        info,
        type,
        apply: isCall ? `${name}()` : name
      }
    })
    for (const child of directChildNamespaces(registry, namespace)) {
      options.push({ label: child, type: 'namespace', apply: child })
    }
    if (!options.length) {
      if (namespace !== 'event') return null
      return {
        from,
        options: [{
          label: '(no sibling project shares this family)',
          type: 'text',
          apply: () => {},
          info: () => completionInfo('event', EVENT_EMPTY_HINT, 'namespace')
        }]
      }
    }
    return { from, options }
  }

  const word = context.matchBefore(/\w*$/)
  if (!word || (word.from === word.to && !context.explicit)) return null
  const namespaces = Object.keys(registry).filter((ns) => !ns.includes('.'))
  const options = namespaces.map((ns) => ({ label: ns, type: 'namespace', apply: ns }))
  if (!options.length) return null
  return { from: word.from, options }
}
