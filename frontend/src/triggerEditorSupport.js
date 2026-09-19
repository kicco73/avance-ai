import { snippetCompletion } from '@codemirror/autocomplete'
import { contributedTriggerNamespaces } from './triggerNamespaces.js'

const CALL_PARAMS = {
  'task.send_mail': ['to', 'body_md'],
  'task.websearch': ['query'],
  'chat.notify': ['title', 'body_md'],
  'chat.show': ['body_md'],
  'chat.chart': ['title', 'bar'],
  'chat.switch_to_human': ['user_id'],
  'drive.read': ['path'],
  'drive.write': ['path', 'text'],
  'drive.list': ['prefix'],
  'drive.delete': ['path']
}

const CORE_NAMESPACE_COLORS = {
  signal: '#1565c0',
  env: '#00838f',
  session: '#6a1b9a',
  'session.metric': '#ad1457',
  user: '#d84315',
  source: '#3949ab',
  task: '#c62828',
  chat: '#f9a825',
  drive: '#4527a0',
  metric: '#2e7d32',
  choice: '#5d4037',
  datetime: '#00695c',
  'datetime.timezone': '#00897b'
}

export function excludingNamespaces(registry, excluded) {
  if (!excluded || !excluded.length) return registry
  const isExcluded = (ns) => excluded.some((x) => ns === x || ns.startsWith(x + '.'))
  return Object.fromEntries(Object.entries(registry).filter(([ns]) => !isExcluded(ns)))
}

const VALUE_NAMESPACES = new Set(['signal', 'env', 'user', 'choice', 'datetime.timezone'])

const CORE_PATTERN_SOURCE = 'signal|env|session(?:\\.metric)?|user|source|task|chat|drive|metric|choice|datetime(?:\\.timezone)?'

class CoreNamespace {
  constructor(name) {
    this._name = name
  }

  get color() {
    return CORE_NAMESPACE_COLORS[this._name] ?? null
  }

  get proxy() {
    return !VALUE_NAMESPACES.has(this._name)
  }

  get emptyHint() {
    return null
  }

  get emptyLabel() {
    return null
  }
}

function namespaceNamed(namespace) {
  const root = namespace.split('.')[0]
  return contributedTriggerNamespaces().find((contributed) => contributed.name === root) ?? new CoreNamespace(namespace)
}

export function namespaceColor(namespace) {
  return namespaceNamed(namespace).color
}

export function isProxyNamespace(namespace) {
  return namespaceNamed(namespace).proxy
}

export function referencePatternSource() {
  const contributed = contributedTriggerNamespaces().map((namespace) => namespace.name)
  return `\\b(${[CORE_PATTERN_SOURCE, ...contributed].join('|')})\\.[A-Za-z_]\\w*`
}

export function namespaceOf(referenceText) {
  const match = new RegExp(`^${referencePatternSource()}`).exec(referenceText)
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
      const contributed = namespaceNamed(namespace)
      const hint = contributed.emptyHint
      if (!hint) return null
      return {
        from,
        options: [{
          label: contributed.emptyLabel,
          type: 'text',
          apply: () => {},
          info: () => completionInfo(namespace, hint, 'namespace')
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
